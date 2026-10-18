"""阶段 C · 覆盖矩阵 / 查询 / 回溯 / 导出 / 复活赛。"""

from __future__ import annotations

import json
from pathlib import Path

from .. import common, db

EXPORTS_DIR = "exports"


def exports_dir() -> Path:
    return common.wpath("exports")


# --- 覆盖矩阵 ---


def _label_enums(labels: dict) -> tuple[list[str], list[str]]:
    return list(labels["target_sections"]), list(labels["use_types"])


def _card_counts(conn) -> dict[tuple[str, str], list[str]]:
    counts: dict[tuple[str, str], list[str]] = {}
    rows = conn.execute(
        "SELECT target_section, use_type, card_id FROM evidence_cards"
        " WHERE stage != 'rejected' ORDER BY card_id"
    ).fetchall()
    for row in rows:
        counts.setdefault((row["target_section"], row["use_type"]), []).append(row["card_id"])
    return counts


def _reclaimable_papers(conn, section: str, use_type: str) -> list[str]:
    """low_priority 中 screening 阶段出现过该用途的论文（可复活候选）。"""
    rows = conn.execute(
        """
        SELECT m.paper_id, m.merged_uses, m.disagreement
        FROM screening_merged m JOIN papers p ON p.paper_id = m.paper_id
        WHERE p.status = 'low_priority'
        ORDER BY m.paper_id
        """
    ).fetchall()
    out: list[str] = []
    for row in rows:
        hit = False
        for field in ("merged_uses", "disagreement"):
            for use in json.loads(row[field] or "[]"):
                if use.get("type") == use_type and use.get("target_section") == section:
                    hit = True
                    break
            if hit:
                break
        if hit:
            out.append(row["paper_id"])
    return out


def _empty_cells(conn, labels: dict, include_background: bool = True) -> list[tuple[str, str]]:
    sections, use_types = _label_enums(labels)
    if not include_background:
        use_types = [t for t in use_types if t != "background"]
    counts = _card_counts(conn)
    return [
        (section, use_type)
        for section in sections
        for use_type in use_types
        if not counts.get((section, use_type))
    ]


def coverage(sections: list[str] | None) -> dict:
    labels = common.load_labels()
    all_sections, use_types = _label_enums(labels)
    sections = sections or all_sections
    conn = db.connect()
    try:
        counts = _card_counts(conn)
        matrix: dict[str, dict[str, dict]] = {}
        for section in sections:
            matrix[section] = {}
            for use_type in use_types:
                card_ids = counts.get((section, use_type), [])
                cell: dict = {"n": len(card_ids), "card_ids": card_ids}
                if not card_ids:
                    reclaimable = _reclaimable_papers(conn, section, use_type)
                    cell["reclaimable"] = bool(reclaimable)
                    cell["reclaimable_papers"] = reclaimable
                matrix[section][use_type] = cell
        return common.ok(sections=sections, use_types=use_types, matrix=matrix)
    finally:
        conn.close()


# --- 查询证据卡 ---


def query(use_type: str, section: str, min_confidence: float) -> dict:
    conn = db.connect()
    try:
        rows = conn.execute(
            """
            SELECT c.*, p.title, p.venue, p.year, p.source,
                   COALESCE(sr.weight, 1.0) AS source_weight
            FROM evidence_cards c
            JOIN papers p ON p.paper_id = c.paper_id
            LEFT JOIN source_reliability sr ON sr.source = p.source
            WHERE c.target_section = ? AND c.use_type = ?
              AND c.stage != 'rejected'
              AND COALESCE(c.confidence, 0.0) >= ?
            ORDER BY COALESCE(c.confidence, 0.0) * COALESCE(sr.weight, 1.0) DESC, c.card_id
            """,
            (section, use_type, min_confidence),
        ).fetchall()
        return common.ok(
            use_type=use_type,
            section=section,
            count=len(rows),
            cards=[dict(r) for r in rows],
        )
    finally:
        conn.close()


def lookup(paper_id: str) -> dict:
    conn = db.connect()
    try:
        paper = conn.execute("SELECT * FROM papers WHERE paper_id = ?", (paper_id,)).fetchone()
        if paper is None:
            raise ValueError(f"unknown paper_id: {paper_id}")

        def all_rows(sql: str, params: tuple = (paper_id,)) -> list[dict]:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

        merged_row = conn.execute(
            "SELECT * FROM screening_merged WHERE paper_id = ?", (paper_id,)
        ).fetchone()
        return common.ok(
            paper=dict(paper),
            ids=all_rows("SELECT id_type, id_value FROM paper_ids WHERE paper_id = ?"),
            votes=all_rows("SELECT agent_id, uses_json, topic_relevant, created_at FROM screening_votes WHERE paper_id = ?"),
            merged=dict(merged_row) if merged_row else None,
            confirmed_uses=all_rows("SELECT use_type, target_section, confidence, needs_fulltext_confirm FROM confirmed_uses WHERE paper_id = ?"),
            relations=all_rows(
                "SELECT src_id, dst_id, relation, is_influential, contexts FROM relations"
                " WHERE src_id = ? OR dst_id = ?",
                (paper_id, paper_id),
            ),
            cards=all_rows("SELECT card_id, use_type, target_section, claim, caveat, stage, locator_status, reject_reason FROM evidence_cards WHERE paper_id = ?"),
            fetch_attempts=all_rows("SELECT source, url, success, error, attempted_at FROM fetch_attempts WHERE paper_id = ?"),
            transitions=all_rows("SELECT from_status, to_status, command, at FROM state_transitions WHERE paper_id = ? ORDER BY id"),
            revivals=all_rows("SELECT from_status, trigger_cell, round, at FROM revival_log WHERE paper_id = ? ORDER BY id"),
        )
    finally:
        conn.close()


# --- 导出 ---

_EXPORT_VIEWS = (
    "screening_log",
    "fail_list",
    "low_priority",
    "discarded",
    "evidence_cards",
    "coverage_matrix",
    "audit_log",
)


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False, default=str) + "\n" for r in rows),
        encoding="utf-8",
    )


def _export_view(conn, view: str, base: Path) -> str:
    if view == "screening_log":
        rows = [dict(r) for r in conn.execute(
            "SELECT p.paper_id, p.title, p.status, m.decision, m.priority FROM papers p"
            " LEFT JOIN screening_merged m ON m.paper_id = p.paper_id ORDER BY p.paper_id"
        )]
        _write_json(base / "screening_log.json", rows)
        return "screening_log.json"
    if view == "fail_list":
        rows = [dict(r) for r in conn.execute(
            "SELECT paper_id, doi, title, source FROM papers WHERE status = 'fetch_failed' ORDER BY paper_id"
        )]
        _write_json(base / "fail_list.json", rows)
        return "fail_list.json"
    if view == "low_priority":
        rows = [dict(r) for r in conn.execute(
            "SELECT paper_id, doi, title, year, venue FROM papers WHERE status = 'low_priority' ORDER BY paper_id"
        )]
        _write_json(base / "low_priority.json", rows)
        return "low_priority.json"
    if view == "discarded":
        rows = [dict(r) for r in conn.execute(
            "SELECT paper_id, doi, title FROM papers WHERE status = 'discarded' ORDER BY paper_id"
        )]
        _write_jsonl(base / "discarded.jsonl", rows)
        return "discarded.jsonl"
    if view == "evidence_cards":
        rows = [dict(r) for r in conn.execute("SELECT * FROM evidence_cards ORDER BY card_id")]
        _write_jsonl(base / "evidence_cards.jsonl", rows)
        return "evidence_cards.jsonl"
    if view == "coverage_matrix":
        _write_json(base / "coverage_matrix.json", coverage(None)["matrix"])
        return "coverage_matrix.json"
    if view == "audit_log":
        rows = [dict(r) for r in conn.execute(
            "SELECT card_id, passes, reason, sampled_at FROM audit_samples ORDER BY sampled_at"
        )]
        _write_jsonl(base / "audit_log.jsonl", rows)
        return "audit_log.jsonl"
    raise ValueError(f"unknown export view: {view!r} (expected one of {list(_EXPORT_VIEWS)})")


def export(view: str | None, base: str | None = None) -> dict:
    conn = db.connect()
    base_path = Path(base) if base else common.wpath("exports")
    try:
        views = [view] if view else list(_EXPORT_VIEWS)
        written = [_export_view(conn, v, base_path) for v in views]
        return common.ok(exports=written)
    finally:
        conn.close()


# --- 复活赛 ---


def _discounted_priority(conn, paper_id: str, weights: dict, discount: float) -> float:
    """按论文 screening 快照里非 background 用途权重之和 × 折扣，作为复活入队优先级。"""
    row = conn.execute(
        "SELECT merged_uses FROM screening_merged WHERE paper_id = ?", (paper_id,)
    ).fetchone()
    total = 0.0
    if row:
        for use in json.loads(row["merged_uses"] or "[]"):
            if use.get("type") != "background":
                total += weights.get(use.get("type"), 0.0)
    return round(total * discount, 6)


def _revive_low_priority(
    conn, paper_id: str, round_no: int, priority: float, trigger: str, now: str
) -> None:
    db.transition(conn, paper_id, "queued", "revive")
    conn.execute(
        """
        INSERT OR REPLACE INTO fetch_queue (paper_id, priority, enqueued_at, claimed_at, claimed_by)
        VALUES (?, ?, ?, NULL, NULL)
        """,
        (paper_id, priority, now),
    )
    conn.execute("UPDATE papers SET revival_rounds = ? WHERE paper_id = ?", (round_no, paper_id))
    conn.execute(
        "INSERT INTO revival_log (paper_id, from_status, trigger_cell, round, at)"
        " VALUES (?, 'low_priority', ?, ?, ?)",
        (paper_id, trigger, round_no, now),
    )


def _revive_discarded(conn, paper_id: str, round_no: int, now: str) -> None:
    """discarded → candidate 重筛：清掉旧票与合并快照，使 next-candidates 重新派发。"""
    db.transition(conn, paper_id, "candidate", "revive")
    conn.execute("DELETE FROM screening_votes WHERE paper_id = ?", (paper_id,))
    conn.execute("DELETE FROM screening_merged WHERE paper_id = ?", (paper_id,))
    conn.execute("DELETE FROM confirmed_uses WHERE paper_id = ?", (paper_id,))
    conn.execute("DELETE FROM fetch_queue WHERE paper_id = ?", (paper_id,))
    conn.execute("UPDATE papers SET revival_rounds = ? WHERE paper_id = ?", (round_no, paper_id))
    conn.execute(
        "INSERT INTO revival_log (paper_id, from_status, trigger_cell, round, at)"
        " VALUES (?, 'discarded', 'manual', ?, ?)",
        (paper_id, round_no, now),
    )


def revive(
    use_type: str | None,
    section: str | None,
    gaps: bool,
    limit: int | None,
    paper_ids: list[str] | None = None,
) -> dict:
    labels = common.load_labels()
    weights = labels["priority_weights"]
    thresholds = labels["thresholds"]
    max_rounds = thresholds["revival_max_rounds"]
    discount = thresholds["revival_discount"]
    paper_ids = list(paper_ids or [])
    conn = db.connect()
    now = common.iso_now()
    try:
        with db.tx(conn):
            revived: list[str] = []
            if paper_ids:
                cells: list[tuple[str, str]] = []
                for paper_id in paper_ids:
                    if limit is not None and len(revived) >= limit:
                        break
                    row = conn.execute(
                        "SELECT status, revival_rounds FROM papers WHERE paper_id = ?", (paper_id,)
                    ).fetchone()
                    if row is None or row["revival_rounds"] >= max_rounds:
                        continue
                    round_no = row["revival_rounds"] + 1
                    if row["status"] == "discarded":
                        _revive_discarded(conn, paper_id, round_no, now)
                        revived.append(paper_id)
                    elif row["status"] == "low_priority":
                        priority = _discounted_priority(conn, paper_id, weights, discount)
                        _revive_low_priority(conn, paper_id, round_no, priority, "manual", now)
                        revived.append(paper_id)
            else:
                if gaps:
                    cells = _empty_cells(conn, labels, include_background=False)
                elif use_type and section:
                    cells = [(section, use_type)]
                else:
                    raise ValueError("revive needs --paper-id, --gaps, or both --type and --section")
                for section_name, use_name in cells:
                    for paper_id in _reclaimable_papers(conn, section_name, use_name):
                        if limit is not None and len(revived) >= limit:
                            break
                        row = conn.execute(
                            "SELECT status, revival_rounds FROM papers WHERE paper_id = ?",
                            (paper_id,),
                        ).fetchone()
                        if row is None or row["status"] != "low_priority":
                            continue
                        if row["revival_rounds"] >= max_rounds:
                            continue
                        round_no = row["revival_rounds"] + 1
                        priority = round(weights.get(use_name, 0.0) * discount, 6)
                        _revive_low_priority(
                            conn, paper_id, round_no, priority, f"{section_name}/{use_name}", now
                        )
                        revived.append(paper_id)
        return common.ok(revived=len(revived), paper_ids=revived, cells=[f"{s}/{u}" for s, u in cells])
    finally:
        conn.close()
