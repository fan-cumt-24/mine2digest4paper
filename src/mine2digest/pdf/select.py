"""确定性页选择：按查询词的字面重叠（TF-IDF 风格）为阅读/反证挑选页文本。

目的：只减少**送入 LLM** 的页文本，locator 校验仍对全文进行，因此不影响证据有效性。
零新依赖，仅用 stdlib。
"""

from __future__ import annotations

import math
import re
from collections import Counter

_WORD_RE = re.compile(r"[A-Za-z0-9_]+")
_STOP = frozenset(
    """a an the of and or to in for on with by as is are was were be been being this that
these those we our you your it its their they them he she his her not no do does did doing
but if then than so such can could should would may might will shall at from into over
under between within without using used use""".split()
)


def tokenize(text: str) -> list[str]:
    """小写分词，去停用词与单字符。"""
    return [
        word
        for word in (m.group(0).lower() for m in _WORD_RE.finditer(text or ""))
        if len(word) > 1 and word not in _STOP
    ]


def resolve_budget(labels: dict, kind: str) -> tuple[int, int]:
    """从 labels.thresholds 读取 (top_k, char_budget)，带兜底默认。"""
    thresholds = labels.get("thresholds", {})
    if kind == "caveat":
        return int(thresholds.get("caveat_top_k", 6)), int(thresholds.get("caveat_char_budget", 8000))
    return int(thresholds.get("reader_top_k", 8)), int(thresholds.get("reader_char_budget", 12000))


def select_pages(
    pages: dict[int, str],
    query: str,
    *,
    top_k: int,
    char_budget: int,
    pin: set[int] | None = None,
) -> list[tuple[int, str]]:
    """按查询词相关度挑选页，返回 (page_no, text)（按原页序，总量 <= char_budget）。

    - 打分 = 查询词在该页的词频 × 跨页 IDF；
    - `pin` 中的页（如 evidence_locator 命中的页）优先入选；
    - 至多 `top_k` 页；每页文本超预算时截断，命中预算即停止。
    """
    if not pages or char_budget <= 0 or top_k <= 0:
        return []
    query_terms = set(tokenize(query))
    ordered = sorted(pages.items())
    counters = {n: Counter(tokenize(t)) for n, t in ordered}
    doc_freq: Counter[str] = Counter()
    for counter in counters.values():
        for word in counter.keys() & query_terms:
            doc_freq[word] += 1

    n_pages = len(ordered)

    def idf(word: str) -> float:
        return math.log((1 + n_pages) / (1 + doc_freq.get(word, 0))) + 1.0

    pinned = set(pin or ())
    scored = [
        (n, text, sum(counters[n][w] * idf(w) for w in query_terms if counters[n].get(w)))
        for n, text in ordered
    ]
    ranked = sorted(scored, key=lambda item: (0 if item[0] in pinned else 1, -item[2], item[0]))

    chosen: dict[int, str] = {}
    used = 0
    for page_no, text, _score in ranked:
        if len(chosen) >= top_k:
            break
        remaining = char_budget - used
        if remaining <= 0:
            break
        snippet = (text or "")[:remaining]
        if not snippet.strip():
            continue
        chosen[page_no] = snippet
        used += len(snippet)
        if used >= char_budget:
            break
    return [(n, chosen[n]) for n in sorted(chosen)]
