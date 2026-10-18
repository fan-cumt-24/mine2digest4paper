"""locator 校验：把 source_span 定位到 PDF 页文本。

- 解析 "p.7, Table 3" -> [7]；解析失败则全页扫描（记录命中页）。
- rapidfuzz.partial_ratio >= locator_fuzz_min 视为 verified。
"""

from __future__ import annotations

import re

from rapidfuzz import fuzz

# p.7 / pp.7 / page 7 / pp. 7-9
_PAGE_RE = re.compile(r"\b(?:pp?|pages?)\.?\s*(\d+)(?:\s*[-–]\s*(\d+))?", re.IGNORECASE)


class LocatorError(Exception):
    """locator 硬门失败（source_span 在页面中找不到足够相似的内容）。"""


def parse_pages(locator: str | None) -> list[int]:
    """从 locator 文本解析页码列表；支持区间 "pp. 7-9"。解析不到返回 []。"""
    if not locator:
        return []
    pages: list[int] = []
    for match in _PAGE_RE.finditer(locator):
        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else start
        if end >= start and end - start <= 50:
            pages.extend(range(start, end + 1))
        else:
            pages.append(start)
    seen: set[int] = set()
    ordered: list[int] = []
    for page in pages:
        if page not in seen:
            seen.add(page)
            ordered.append(page)
    return ordered


def check_span(
    source_span: str,
    page_texts: dict[int, str],
    parsed_pages: list[int] | None = None,
    fuzz_min: int = 75,
) -> tuple[str, float, str | None, int | None]:
    """返回 (status, match_ratio, matched_text, page)。

    status ∈ {verified, unverified}。有解析页码时只扫这些页，否则扫全部页。
    """
    candidates = parsed_pages or sorted(page_texts.keys())
    best_score = -1.0
    best_text: str | None = None
    best_page: int | None = None
    for page in candidates:
        text = page_texts.get(page)
        if not text:
            continue
        alignment = fuzz.partial_ratio_alignment(source_span, text)
        if alignment is None:
            continue
        if alignment.score > best_score:
            best_score = alignment.score
            best_text = text[alignment.dest_start : alignment.dest_end]
            best_page = page
    if best_score < 0:
        return "unverified", 0.0, None, None
    status = "verified" if best_score >= fuzz_min else "unverified"
    return status, float(best_score), best_text, best_page
