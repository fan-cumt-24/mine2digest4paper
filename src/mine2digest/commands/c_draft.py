"""阶段 C4 · 逐节草稿 + evidence_ref 回溯校验。

- `draft`：读结构模板 + 覆盖矩阵 + 证据卡，生成“证据绑定骨架”和给宿主 agent 的
  打磨 prompt；可选调用外部命令直接产出成稿。
- `verify`：扫描草稿里的 `[[card:<id>]]`，独立校验回溯有效性。
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from pathlib import Path

from .. import common, db
from . import c_templates

DRAFTS_DIR = "drafts"
REF_RE = re.compile(r"\[\[card:([A-Za-z0-9._:-]+)\]\]")


def drafts_dir() -> Path:
    return common.wpath("drafts")
CLAIMS_HEADING = "## Claims"


# --- draft ---


def _section_cards(conn, section: str, min_confidence: float) -> list[dict]:
    rows = conn.execute(
        """
        SELECT c.*, p.title, p.venue, p.year, p.source,
               COALESCE(sr.weight, 1.0) AS source_weight
        FROM evidence_cards c
        JOIN papers p ON p.paper_id = c.paper_id
        LEFT JOIN source_reliability sr ON sr.source = p.source
        WHERE c.target_section = ? AND c.stage != 'rejected' AND p.status != 'rejected'
          AND COALESCE(c.confidence, 0.0) >= ?
        ORDER BY COALESCE(c.confidence, 0.0) * COALESCE(sr.weight, 1.0) DESC, c.card_id
        """,
        (section, min_confidence),
    ).fetchall()
    return [dict(r) for r in rows]


def _load_template(conn, section: str) -> dict | None:
    path = c_templates.templates_path()
    if not path.exists():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    for tpl in doc.get("templates", []):
        if tpl.get("target_section") != section:
            continue
        pid = tpl.get("source_paper_id")
        if pid:
            row = conn.execute("SELECT status FROM papers WHERE paper_id = ?", (pid,)).fetchone()
            if row is not None and row["status"] == "rejected":
                return None
        return tpl
    return None


def _one_line(text: str | None) -> str:
    return " ".join((text or "").split())


def _render_skeleton(
    section: str,
    cards: list[dict],
    template: dict | None,
    gaps: list[str],
    use_types: list[str],
    now: str,
) -> str:
    by_type: dict[str, list[dict]] = {}
    for card in cards:
        by_type.setdefault(card["use_type"], []).append(card)

    lines = [f"# Draft: {section}", ""]
    if template:
        tpl_line = f"- template: {template.get('source_paper_id')} (provisional={bool(template.get('provisional'))})"
    else:
        tpl_line = "- template: none"
    lines.extend([f"- generated_at: {now}", tpl_line, f"- claims: {len(cards)}", ""])
    lines.append(CLAIMS_HEADING)
    lines.append("")

    if not cards:
        lines.extend(["_（本节暂无证据卡）_", ""])
    for use_type in use_types:
        group = by_type.get(use_type)
        if not group:
            continue
        lines.append(f"### {use_type}")
        for card in group:
            claim = _one_line(card.get("claim")) or "(no claim)"
            lines.append(f"- {claim} [[card:{card['card_id']}]]")
            caveat = _one_line(card.get("caveat"))
            if caveat:
                lines.append(f"  - caveat: {caveat}")
        lines.append("")

    lines.append("## Gaps")
    lines.extend([f"- {gap}" for gap in gaps] or ["- none"])
    lines.append("")

    if template and template.get("source_span"):
        lines.append("## Style Reference")
        for line in str(template["source_span"]).splitlines():
            lines.append(f"> {line}")
        lines.append(f"> — from {template.get('source_paper_id')} ({template.get('title') or ''})")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _render_prompt(section: str, skeleton: str) -> str:
    return f"""# Draft polish task: {section}

你是论文写作助手。把下面的“证据绑定骨架”打磨成通顺的 `{section}` 小节。

## 硬约束

- 每一条 claim 必须保留原有的 `[[card:<card_id>]]` 引用；不得删除、改写、合并或臆造引用。
- 不得引入没有 evidence_ref 的新 claim。
- `## Gaps` 中的缺口保持显式，不得编造内容填补。
- 只输出 Markdown 正文，不要代码围栏，不要解释。

## Evidence-bound skeleton

{skeleton}"""


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


def draft(
    section: str,
    min_confidence: float = 0.0,
    out_dir: str | Path | None = None,
    polish: bool = False,
    polish_cmd: str | None = None,
) -> dict:
    labels = common.load_labels()
    if section not in set(labels["target_sections"]):
        raise ValueError(f"illegal section: {section!r}")
    use_types = list(labels["use_types"])
    now = common.iso_now()

    conn = db.connect()
    try:
        cards = _section_cards(conn, section, min_confidence)
        template = _load_template(conn, section)
    finally:
        conn.close()

    present = {card["use_type"] for card in cards}
    gaps = [use_type for use_type in use_types if use_type not in present]

    out = Path(out_dir) if out_dir else drafts_dir()
    out.mkdir(parents=True, exist_ok=True)
    skeleton_path = out / f"{section}.md"
    prompt_path = out / f"{section}.prompt.md"

    skeleton = _render_skeleton(section, cards, template, gaps, use_types, now)
    prompt = _render_prompt(section, skeleton)
    skeleton_path.write_text(skeleton, encoding="utf-8")
    prompt_path.write_text(prompt, encoding="utf-8")

    files = [str(skeleton_path), str(prompt_path)]
    polished_path: Path | None = None

    if polish:
        cmd_text = polish_cmd or os.environ.get("MINE2DIGEST_DRAFT_CMD")
        if not cmd_text:
            return common.err(
                "polish command not configured (set MINE2DIGEST_DRAFT_CMD or --polish-cmd)",
                section=section,
                files=files,
            )
        timeout = int(os.environ.get("MINE2DIGEST_DRAFT_TIMEOUT", "300"))
        try:
            polished = _run_polish(cmd_text, prompt, timeout)
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
            return common.err(str(exc), section=section, files=files)
        if not polished.strip():
            return common.err("polish command produced empty output", section=section, files=files)
        polished_path = out / f"{section}.polished.md"
        polished_path.write_text(polished, encoding="utf-8")
        files.append(str(polished_path))

    return common.ok(
        section=section,
        files=files,
        skeleton=str(skeleton_path),
        prompt=str(prompt_path),
        polished=str(polished_path) if polished_path else None,
        claims=len(cards),
        cards=len(cards),
        gaps=gaps,
        template=template,
    )


# --- verify ---


def _unreferenced_claims(text: str) -> list[dict]:
    """返回 `## Claims` 下无 evidence_ref 的顶层 bullet。"""
    out: list[dict] = []
    in_claims = False
    in_fence = False
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip()
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if stripped.startswith("## "):
            in_claims = stripped == CLAIMS_HEADING
            continue
        if not in_claims:
            continue
        if re.match(r"^[-*]\s+", line) and not REF_RE.search(line):
            out.append({"line": lineno, "text": stripped[:120]})
    return out


def _validate_ref(conn, card_id: str, fuzz_min: int, recheck: bool) -> dict | None:
    """校验单条 ref；通过返回 None，否则返回 {card_id, reason}。"""
    row = conn.execute(
        """
        SELECT c.*, p.status AS paper_status
        FROM evidence_cards c JOIN papers p ON p.paper_id = c.paper_id
        WHERE c.card_id = ?
        """,
        (card_id,),
    ).fetchone()
    if row is None:
        return {"card_id": card_id, "reason": "unknown card"}
    if row["stage"] == "rejected":
        return {"card_id": card_id, "reason": "card rejected"}
    if row["paper_status"] == "rejected":
        return {"card_id": card_id, "reason": "paper rejected"}

    if recheck:
        from ..pdf import locator
        from .b_read import _page_texts_for

        try:
            page_texts = _page_texts_for(conn, row["paper_id"])
            parsed = locator.parse_pages(row["evidence_locator"])
            status, ratio, _matched, _page = locator.check_span(
                row["source_span"] or "", page_texts, parsed, fuzz_min
            )
        except Exception as exc:  # noqa: BLE001 - 重跑依赖 PDF，失败即视为无效
            return {"card_id": card_id, "reason": f"recheck failed: {exc}"}
        if status != "verified" or ratio < fuzz_min:
            return {"card_id": card_id, "reason": f"locator ratio {ratio:.1f} < {fuzz_min}"}
        return None

    if row["locator_status"] != "verified":
        return {"card_id": card_id, "reason": f"locator status {row['locator_status']!r}"}
    check = conn.execute(
        "SELECT match_ratio FROM locator_checks WHERE card_id = ?", (card_id,)
    ).fetchone()
    if check is None:
        return {"card_id": card_id, "reason": "locator check missing"}
    ratio = float(check["match_ratio"] or 0.0)
    if ratio < fuzz_min:
        return {"card_id": card_id, "reason": f"locator ratio {ratio:.1f} < {fuzz_min}"}
    return None


def verify(file: str, require_refs: bool = False, recheck: bool = False) -> dict:
    path = Path(file)
    if not path.is_file():
        raise ValueError(f"draft file not found: {file}")
    text = path.read_text(encoding="utf-8")
    labels = common.load_labels()
    fuzz_min = labels["thresholds"]["locator_fuzz_min"]

    all_refs = REF_RE.findall(text)
    unique_refs = list(dict.fromkeys(all_refs))
    unreferenced = _unreferenced_claims(text)

    valid: list[str] = []
    invalid: list[dict] = []
    conn = db.connect()
    try:
        for card_id in unique_refs:
            problem = _validate_ref(conn, card_id, fuzz_min, recheck)
            if problem is None:
                valid.append(card_id)
            else:
                invalid.append(problem)
    finally:
        conn.close()

    payload = {
        "file": str(path),
        "refs": len(all_refs),
        "unique_refs": len(unique_refs),
        "valid": len(valid),
        "invalid": invalid,
        "unreferenced": unreferenced,
    }
    if invalid:
        return common.err("invalid evidence_ref", **payload)
    if require_refs and unreferenced:
        return common.err("unreferenced claims", **payload)
    return common.ok(**payload)
