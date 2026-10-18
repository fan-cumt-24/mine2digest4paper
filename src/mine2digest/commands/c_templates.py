"""阶段 C1 · 结构模板抽取（style_sample 优先，venue 回退标 provisional）。"""

from __future__ import annotations

import json
from pathlib import Path

from .. import common, db

TEMPLATES_PATH = "outputs/structure_templates.json"


def templates_path() -> Path:
    return common.wpath("outputs", "structure_templates.json")


def _target_venue(conn) -> str | None:
    """已确认 style_sample 卡里出现最多的 venue（作为回退匹配的目标 venue）。"""
    row = conn.execute(
        """
        SELECT p.venue AS venue, COUNT(*) AS n
        FROM evidence_cards c JOIN papers p ON p.paper_id = c.paper_id
        WHERE c.use_type = 'style_sample' AND c.stage = 'fulltext_confirmed'
          AND p.status != 'rejected' AND p.venue IS NOT NULL AND p.venue != ''
        GROUP BY p.venue
        ORDER BY n DESC, p.venue
        LIMIT 1
        """
    ).fetchone()
    return row["venue"] if row else None


def _pick_confirmed(conn, section: str) -> dict | None:
    row = conn.execute(
        """
        SELECT c.card_id, c.paper_id, c.source_span, c.confidence, c.claim, p.title, p.venue
        FROM evidence_cards c JOIN papers p ON p.paper_id = c.paper_id
        WHERE c.use_type = 'style_sample' AND c.target_section = ?
          AND c.stage = 'fulltext_confirmed' AND p.status != 'rejected'
        ORDER BY c.confidence DESC NULLS LAST, c.card_id
        LIMIT 1
        """,
        (section,),
    ).fetchone()
    if row is None:
        return None
    return {
        "target_section": section,
        "source_paper_id": row["paper_id"],
        "card_id": row["card_id"],
        "claim": row["claim"],
        "source_span": row["source_span"],
        "confidence": row["confidence"],
        "title": row["title"],
        "venue": row["venue"],
        "provisional": False,
    }


def _pick_provisional(conn, section: str, venue: str | None) -> dict | None:
    if not venue:
        return None
    row = conn.execute(
        """
        SELECT paper_id, title, venue FROM papers
        WHERE status = 'fetched' AND venue = ?
        ORDER BY updated_at DESC, paper_id
        LIMIT 1
        """,
        (venue,),
    ).fetchone()
    if row is None:
        return None
    return {
        "target_section": section,
        "source_paper_id": row["paper_id"],
        "card_id": None,
        "claim": None,
        "source_span": None,
        "confidence": None,
        "title": row["title"],
        "venue": row["venue"],
        "provisional": True,
    }


def extract_templates(sections: list[str] | None = None, path: str | Path | None = None) -> dict:
    labels = common.load_labels()
    all_sections = list(labels["target_sections"])
    sections = sections or all_sections
    conn = db.connect()
    try:
        target_venue = _target_venue(conn)
        templates = []
        for section in sections:
            tpl = _pick_confirmed(conn, section) or _pick_provisional(conn, section, target_venue)
            if tpl is not None:
                templates.append(tpl)
        out = Path(path) if path else templates_path()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(
                {"generated_at": common.iso_now(), "target_venue": target_venue, "templates": templates},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return common.ok(
            path=str(out), count=len(templates), target_venue=target_venue, templates=templates
        )
    finally:
        conn.close()
