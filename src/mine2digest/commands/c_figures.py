"""阶段 C3 · 图表设计建议：从优秀论文的“图表语料”出发给出设计建议。

不再生成图片（原 PaperBanana 包装已移除），改为：
- `extract`：确定性抽取已归档论文的图注/表注 + 引用上下文 -> outputs/figure_corpus.json；
  可选 `--render` 把图注所在页渲染成 PNG，供**支持视觉的宿主模型读图**抽取设计。
- `suggest`：给定你自己的图表意图，按词面相关度挑参考图，产出设计建议骨架 + 打磨 prompt；
  可选 `--polish` 用外部命令直出成稿（与 `draft` 一致）。
- `add-notes`：把视觉模型读图得到的观察（FigureNote）落盘到 outputs/figures/design_notes.json。
- `verify`：校验建议里的 `[fig:<ref>]` 是否都能在语料/观察中找到。
"""

from __future__ import annotations

import json
import math
import os
import re
import shlex
import subprocess
from collections import Counter
from pathlib import Path

from .. import common, db, store
from ..models import FigureNote, FigureNoteFile
from ..pdf import extract as pdf_extract
from ..pdf import render as pdf_render
from ..pdf import select
from . import c_templates

CORPUS_NAME = "figure_corpus.json"
NOTES_NAME = "design_notes.json"
FIGURES_SUBDIR = ("outputs", "figures")
REF_RE = re.compile(r"\[fig:([^\]]+)\]")

_CAPTION_RE = re.compile(
    r"(?i)^[ \t]*(figure|fig\.?|table)[ \t]*([0-9]+)[ \t]*[.:]?[ \t]*"
)
_HEADING_RE = re.compile(
    r"(?i)^[ \t]*(?:[0-9]+(?:\.[0-9]+)*\.?[ \t]+\S|abstract\b|references\b|acknowledg)"
)
_MAX_CAPTION = 400


def figures_dir() -> Path:
    return common.wpath(*FIGURES_SUBDIR)


def corpus_path() -> Path:
    return common.wpath("outputs", CORPUS_NAME)


def notes_path() -> Path:
    return common.wpath("outputs", FIGURES_SUBDIR[1], NOTES_NAME)


def pages_dir() -> Path:
    return common.wpath(*FIGURES_SUBDIR, "pages")


# --- 语料抽取（确定性） ---


def _paper_order(conn) -> tuple[list[dict], str | None]:
    """返回按“style_sample 优先、其次目标 venue”排序的论文元数据，及目标 venue。"""
    rows = [dict(r) for r in conn.execute(
        "SELECT paper_id, title, venue, year FROM papers WHERE status != 'rejected'"
    )]
    style_ids = {
        r["paper_id"]
        for r in conn.execute(
            "SELECT DISTINCT paper_id FROM evidence_cards"
            " WHERE use_type = 'style_sample' AND stage = 'fulltext_confirmed'"
        )
    }
    target_venue = c_templates._target_venue(conn)

    def rank(paper: dict) -> tuple[int, str]:
        if paper["paper_id"] in style_ids:
            return (0, paper["paper_id"])
        if target_venue and paper.get("venue") == target_venue:
            return (1, paper["paper_id"])
        return (2, paper["paper_id"])

    rows.sort(key=rank)
    return rows, target_venue


def _is_heading(line: str) -> bool:
    return bool(_HEADING_RE.match(line))


def _page_captions(text: str) -> list[tuple[str, int, str, int, int]]:
    """从一页文本抽取 (kind, number, caption, span_start, span_end)。"""
    lines = text.splitlines(keepends=True)
    starts: list[int] = []
    pos = 0
    for line in lines:
        starts.append(pos)
        pos += len(line)

    out: list[tuple[str, int, str, int, int]] = []
    i = 0
    while i < len(lines):
        m = _CAPTION_RE.match(lines[i])
        if not m:
            i += 1
            continue
        kind = "figure" if m.group(1).lower().startswith("fig") else "table"
        number = int(m.group(2))
        parts = [lines[i][m.end():].strip()]
        j = i + 1
        while j < len(lines) and sum(len(p) for p in parts) < _MAX_CAPTION:
            nxt = lines[j].strip()
            if not nxt or _CAPTION_RE.match(lines[j]) or _is_heading(nxt):
                break
            parts.append(nxt)
            j += 1
        caption = " ".join(p for p in parts if p).strip()[:_MAX_CAPTION]
        span_start = starts[i]
        span_end = starts[j] if j < len(lines) else starts[i] + len(lines[i])
        out.append((kind, number, caption, span_start, span_end))
        i = max(j, i + 1)
    return out


def _context(pages: dict[int, str], page_no: int, kind: str, number: int, span: tuple[int, int]) -> str:
    """找该图/表在正文里的引用句（排除图注自身）。"""
    pattern = re.compile(
        (r"fig(?:ure)?s?\.?[ \t]*%d\b" if kind == "figure" else r"tables?[ \t]*%d\b") % number,
        re.I,
    )
    order = [page_no] + [p for p in sorted(pages) if p != page_no]
    for p in order:
        text = pages.get(p, "")
        for m in pattern.finditer(text):
            if p == page_no and span[0] <= m.start() < span[1]:
                continue
            s = max(0, m.start() - 160)
            e = min(len(text), m.end() + 160)
            snippet = " ".join(text[s:e].split())
            if snippet:
                return snippet
    return ""


def extract_figures(
    paper_ids: list[str] | None = None,
    kind: str = "both",
    limit: int | None = None,
    render: bool = False,
    dpi: int = 150,
    out: str | Path | None = None,
) -> dict:
    if kind not in ("figure", "table", "both"):
        raise ValueError(f"illegal kind: {kind!r} (figure|table|both)")

    conn = db.connect()
    try:
        rows, target_venue = _paper_order(conn)
    finally:
        conn.close()

    wanted = set(paper_ids or [])
    by_ref: dict[str, dict] = {}
    rendered: dict[tuple[str, int], str] = {}

    for paper in rows:
        pid = paper["paper_id"]
        if wanted and pid not in wanted:
            continue
        paper_dir = store.locate(pid, paper["title"], paper["year"])
        pages = pdf_extract.load_page_texts(paper_dir / pdf_extract.EXTRACTED_DIR)
        if not pages and (paper_dir / "paper.pdf").exists():
            pdf_extract.ensure_extracted(paper_dir)
            pages = pdf_extract.load_page_texts(paper_dir / pdf_extract.EXTRACTED_DIR)
        if not pages:
            continue
        for page_no in sorted(pages):
            for cap_kind, number, caption, span_start, span_end in _page_captions(pages[page_no]):
                if kind != "both" and cap_kind != kind:
                    continue
                label = ("Figure" if cap_kind == "figure" else "Table") + f" {number}"
                ref = f"{pid}#{label}"
                prev = by_ref.get(ref)
                if prev is not None:
                    if len(caption) > len(prev["caption"]):
                        prev["caption"] = caption
                    if not prev.get("context"):
                        prev["context"] = _context(
                            pages, page_no, cap_kind, number, (span_start, span_end)
                        )
                    continue
                entry = {
                    "ref": ref,
                    "paper_id": pid,
                    "title": paper["title"],
                    "venue": paper["venue"],
                    "year": paper["year"],
                    "kind": cap_kind,
                    "number": number,
                    "label": label,
                    "caption": caption,
                    "context": _context(pages, page_no, cap_kind, number, (span_start, span_end)),
                    "page": page_no,
                }
                if render:
                    key = (pid, page_no)
                    if key not in rendered:
                        pdf_path = paper_dir / "paper.pdf"
                        dest = pages_dir() / f"{store.slugify(pid, paper['title'], paper['year'])}-p{page_no}.png"
                        pdf_render.render_page(pdf_path, page_no, dest, dpi=dpi)
                        rendered[key] = str(dest.relative_to(common.workspace())) if _under_ws(dest) else str(dest)
                    entry["page_image"] = rendered[key]
                by_ref[ref] = entry
                if limit is not None and len(by_ref) >= limit:
                    break
            if limit is not None and len(by_ref) >= limit:
                break
        if limit is not None and len(by_ref) >= limit:
            break

    entries = list(by_ref.values())

    dest = Path(out) if out else corpus_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "generated_at": common.iso_now(),
        "target_venue": target_venue,
        "rendered": render,
        "count": len(entries),
        "entries": entries,
    }
    dest.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return common.ok(
        path=str(dest),
        count=len(entries),
        target_venue=target_venue,
        figure_count=sum(1 for e in entries if e["kind"] == "figure"),
        table_count=sum(1 for e in entries if e["kind"] == "table"),
        rendered=len(rendered) if render else 0,
        pages_dir=str(pages_dir()) if render else None,
    )


def _under_ws(path: Path) -> bool:
    try:
        path.resolve().relative_to(common.workspace().resolve())
        return True
    except ValueError:
        return False


# --- 设计建议（挑选参考 + 骨架 + prompt） ---


def _load_corpus(path: str | Path | None = None) -> dict:
    p = Path(path) if path else corpus_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _rank(entries: list[dict], query: str, top_k: int, char_budget: int) -> list[dict]:
    """按与 query 的词面相关度挑参考图，受 top_k 与字符预算约束。"""
    if not entries or top_k <= 0 or char_budget <= 0:
        return []
    texts = [f"{e.get('caption', '')} {e.get('context', '')}" for e in entries]
    counters = [Counter(select.tokenize(t)) for t in texts]
    query_terms = set(select.tokenize(query))
    doc_freq: Counter[str] = Counter()
    for counter in counters:
        for word in counter.keys() & query_terms:
            doc_freq[word] += 1
    n = len(entries)

    def idf(word: str) -> float:
        return math.log((1 + n) / (1 + doc_freq.get(word, 0))) + 1.0

    scores = [sum(counters[i][w] * idf(w) for w in query_terms if counters[i].get(w)) for i in range(n)]
    order = sorted(range(n), key=lambda i: (-scores[i], entries[i].get("ref", "")))

    chosen: list[dict] = []
    used = 0
    for i in order:
        if len(chosen) >= top_k:
            break
        size = len(texts[i]) + 1
        if chosen and used + size > char_budget:
            continue
        chosen.append(entries[i])
        used += size
        if used >= char_budget:
            break
    return chosen


def _load_rubric() -> str:
    path = common.assets_root() / "figure_prompts" / "designer.md"
    if path.exists():
        try:
            return path.read_text(encoding="utf-8").strip()
        except OSError:
            pass
    return (
        "你是论文图表设计顾问。根据“参考设计”（优秀论文的图注与引用上下文）和用户意图，"
        "给出可执行的图表设计建议：推荐图型、分面板 (a)/(b)、编码约定（颜色/箭头/标注）、"
        "图注提纲，以及每条建议背后的参考图 `[fig:<ref>]`。"
    )


def _one_line(text: str | None, limit: int = 200) -> str:
    return " ".join((text or "").split())[:limit]


def _render_skeleton(
    fig_id: str,
    intent: str,
    refs: list[dict],
    now: str,
    meta: dict,
) -> str:
    lines = [f"# Figure design suggestion: {fig_id}", ""]
    lines.append(f"- intent: {_one_line(intent, 300) or '(none)'}")
    lines.append(f"- generated_at: {now}")
    lines.append(f"- section: {meta.get('section') or 'n/a'}")
    lines.append(f"- kind: {meta.get('kind') or 'both'}")
    lines.append(f"- references: {len(refs)} (from {meta.get('corpus')})")
    lines.append("")
    lines.append("## Recommended type")
    lines.append("")
    lines.append("_待填：overview/pipeline · 对比柱状 · 消融表 · 热力图 · 定性网格 · 架构图_")
    lines.append("")
    lines.append("## Panels")
    lines.append("")
    lines.append("- (a) ...")
    lines.append("- (b) ...")
    lines.append("")
    lines.append("## Encodings")
    lines.append("")
    lines.append("- color / arrow / annotation ...")
    lines.append("")
    lines.append("## Caption outline")
    lines.append("")
    lines.append("- ...")
    lines.append("")
    lines.append("## Reference designs")
    lines.append("")
    if not refs:
        lines.append("_（语料为空：先跑 `figures extract`，或放宽 --kind / --paper-id）_")
    for entry in refs:
        img = f" (page_image: {entry['page_image']})" if entry.get("page_image") else ""
        lines.append(f"- [fig:{entry['ref']}] {_one_line(entry.get('caption'))}{img}")
        ctx = _one_line(entry.get("context"))
        if ctx:
            lines.append(f"  - context: {ctx}")
    lines.append("")
    lines.append("## Risks / pitfalls")
    lines.append("")
    lines.append("- ...")
    return "\n".join(lines).rstrip() + "\n"


def _render_prompt(fig_id: str, intent: str, refs: list[dict], rubric: str, meta: dict) -> str:
    ref_blocks = []
    for entry in refs:
        block = [f"### [fig:{entry['ref']}]", f"- paper: {entry.get('title')} ({entry.get('venue')} {entry.get('year')})"]
        block.append(f"- {entry.get('label')}: {_one_line(entry.get('caption'), 400)}")
        if entry.get("context"):
            block.append(f"- context: {_one_line(entry.get('context'), 400)}")
        if entry.get("page_image"):
            block.append(f"- page_image: {entry['page_image']}")
        ref_blocks.append("\n".join(block))
    references = "\n\n".join(ref_blocks) or "_(无参考图)_"
    vision = (
        "若你能读图：先 `Read` 每个参考的 `page_image`，观察其版面/面板/配色后再写设计，"
        "并在 `applicable_to` 里注明可迁移到你的哪张图。"
    )
    return f"""# Figure design task: {fig_id}

{rubric}

## 用户意图

{intent.strip() or '(none)'}

## 目标

- section: {meta.get('section') or 'n/a'}
- kind: {meta.get('kind') or 'both'}

## 硬约束

- 每条设计建议尽量以 `[fig:<ref>]` 引用下面的参考设计；**只能引用给定 ref**，不得臆造。
- {vision}
- 只输出 Markdown 正文（含 `## Recommended type` / `## Panels` / `## Encodings` /
  `## Caption outline` / `## Reference designs`），不要代码围栏，不要解释。

## Reference designs（来自优秀论文的图表语料）

{references}"""


def suggest_figures(
    input_file: str,
    section: str | None = None,
    kind: str | None = None,
    fig_id: str | None = None,
    top_k: int | None = None,
    char_budget: int | None = None,
    out_dir: str | Path | None = None,
    polish: bool = False,
    polish_cmd: str | None = None,
    corpus: str | Path | None = None,
) -> dict:
    input_path = Path(input_file)
    if not input_path.is_file():
        raise ValueError(f"input file not found: {input_file}")
    if section is not None:
        labels = common.load_labels()
        if section not in set(labels["target_sections"]):
            raise ValueError(f"illegal section: {section!r}")

    labels = common.load_labels()
    default_top_k, default_budget = select.resolve_budget(labels, "figure")
    top_k = top_k if top_k is not None else default_top_k
    char_budget = char_budget if char_budget is not None else default_budget

    doc = _load_corpus(corpus)
    if not doc:
        extract_figures()
        doc = _load_corpus(corpus)
    entries = list(doc.get("entries", []))
    if kind:
        entries = [e for e in entries if e.get("kind") == kind]

    intent = input_path.read_text(encoding="utf-8")
    refs = _rank(entries, f"{intent}", top_k, char_budget)

    fig_id = fig_id or _default_fig_id(input_path)
    now = common.iso_now()
    meta = {
        "section": section,
        "kind": kind,
        "corpus": str(corpus or corpus_path()),
    }

    out = Path(out_dir) if out_dir else figures_dir()
    out.mkdir(parents=True, exist_ok=True)
    skeleton_path = out / f"{fig_id}.suggestion.md"
    prompt_path = out / f"{fig_id}.prompt.md"

    skeleton = _render_skeleton(fig_id, intent, refs, now, meta)
    prompt = _render_prompt(fig_id, intent, refs, _load_rubric(), meta)
    skeleton_path.write_text(skeleton, encoding="utf-8")
    prompt_path.write_text(prompt, encoding="utf-8")

    files = [str(skeleton_path), str(prompt_path)]
    polished_path: Path | None = None
    if polish:
        cmd_text = polish_cmd or os.environ.get("MINE2DIGEST_FIGURE_CMD")
        if not cmd_text:
            return common.err(
                "polish command not configured (set MINE2DIGEST_FIGURE_CMD or --polish-cmd)",
                fig_id=fig_id,
                files=files,
            )
        timeout = int(os.environ.get("MINE2DIGEST_FIGURE_TIMEOUT", "300"))
        try:
            polished = _run_polish(cmd_text, prompt, timeout)
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
            return common.err(str(exc), fig_id=fig_id, files=files)
        if not polished.strip():
            return common.err("polish command produced empty output", fig_id=fig_id, files=files)
        polished_path = out / f"{fig_id}.polished.md"
        polished_path.write_text(polished, encoding="utf-8")
        files.append(str(polished_path))

    return common.ok(
        fig_id=fig_id,
        files=files,
        skeleton=str(skeleton_path),
        prompt=str(prompt_path),
        polished=str(polished_path) if polished_path else None,
        references=len(refs),
        refs=[e["ref"] for e in refs],
        corpus=str(corpus or corpus_path()),
        top_k=top_k,
        char_budget=char_budget,
    )


def _default_fig_id(input_path: Path) -> str:
    stem = re.sub(r"[^0-9a-zA-Z]+", "-", input_path.stem).strip("-").lower() or "figure"
    return f"fig-{stem}"


def _run_polish(cmd_text: str, prompt: str, timeout: int) -> str:
    cmd = shlex.split(cmd_text)
    if not cmd:
        raise ValueError("empty polish command")
    proc = subprocess.run(
        cmd,
        input=prompt,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[-500:]
        raise RuntimeError(f"polish command failed (exit {proc.returncode}): {detail}")
    return proc.stdout


# --- 读图观察（结构化落盘） ---


def _as_notes(payload) -> list[FigureNote]:
    if isinstance(payload, dict) and "notes" in payload:
        return FigureNoteFile(**payload).notes
    if isinstance(payload, dict):
        return [FigureNote(**payload)]
    if isinstance(payload, list):
        return [FigureNote(**item) for item in payload]
    raise ValueError("unrecognized note payload (expected object, {notes:[...]}, or list)")


def add_notes(file: str | None) -> dict:
    doc = _load_corpus()
    if not doc:
        return common.err("figure corpus not found; run `figures extract` first")
    known = {e["ref"] for e in doc.get("entries", [])}
    notes = _as_notes(common.parse_json(common.read_input(file)))

    for note in notes:
        if note.ref not in known:
            raise ValueError(f"unknown figure ref: {note.ref!r} (not in figure_corpus.json)")

    dest = notes_path()
    existing = _load_notes()
    by_ref = {n["ref"]: n for n in existing.get("notes", [])}
    for note in notes:
        by_ref[note.ref] = note.model_dump()
    merged = sorted(by_ref.values(), key=lambda n: n["ref"])
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        json.dumps(
            {"generated_at": common.iso_now(), "count": len(merged), "notes": merged},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return common.ok(path=str(dest), count=len(notes), total=len(merged))


def _load_notes() -> dict:
    path = notes_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


# --- 校验建议里的引用 ---


def verify_figures(file: str) -> dict:
    path = Path(file)
    if not path.is_file():
        raise ValueError(f"suggestion file not found: {file}")
    text = path.read_text(encoding="utf-8")

    known = {e["ref"] for e in _load_corpus().get("entries", [])}
    known |= {n["ref"] for n in _load_notes().get("notes", [])}

    all_refs = REF_RE.findall(text)
    unique_refs = list(dict.fromkeys(all_refs))
    valid = [r for r in unique_refs if r in known]
    invalid = [r for r in unique_refs if r not in known]

    payload = {
        "file": str(path),
        "refs": len(all_refs),
        "unique_refs": len(unique_refs),
        "valid": len(valid),
        "invalid": invalid,
    }
    if invalid:
        return common.err("invalid figure ref", **payload)
    return common.ok(**payload)
