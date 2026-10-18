"""阶段 B · 阅读：next-reading / add-card / verify-card。"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any

from .. import common, db, store
from ..models import Card, CardFile
from ..pdf import extract, locator, select


def safe_id(value: str) -> str:
    """把 paper_id/card_id 变成可用于文件名的安全串。"""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value) or "item"


def write_payload(out_dir: str | Path, name: str, payload: dict) -> tuple[Path, int]:
    """把一个条目 payload 落盘，返回 (路径, 字符数)。"""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    target = out / name
    text = json.dumps(payload, ensure_ascii=False)
    target.write_text(text, encoding="utf-8")
    return target, len(text)


def _as_cards(payload: Any) -> list[Card]:
    if isinstance(payload, dict) and "cards" in payload:
        return CardFile(**payload).cards
    if isinstance(payload, dict):
        return [Card(**payload)]
    if isinstance(payload, list):
        return [Card(**item) for item in payload]
    raise ValueError("unrecognized card payload (expected object, {cards:[...]}, or list)")


def _validate_enums(labels: dict, card: Card) -> None:
    if card.use_type not in set(labels["use_types"]):
        raise ValueError(f"illegal use type: {card.use_type!r}")
    if card.target_section not in set(labels["target_sections"]):
        raise ValueError(f"illegal target_section: {card.target_section!r}")


def _page_texts_for(conn, paper_id: str) -> dict[int, str]:
    paper = conn.execute(
        "SELECT title, year FROM papers WHERE paper_id = ?", (paper_id,)
    ).fetchone()
    if paper is None:
        raise ValueError(f"unknown paper_id: {paper_id}")
    paper_dir = store.locate(paper_id, paper["title"], paper["year"])
    extract.ensure_extracted(paper_dir)
    return extract.load_page_texts(paper_dir / extract.EXTRACTED_DIR)


def next_reading(
    limit: int,
    out_dir: str | Path | None = None,
    char_budget: int | None = None,
    top_k: int | None = None,
) -> dict:
    labels = common.load_labels()
    default_top_k, default_budget = select.resolve_budget(labels, "reader")
    top_k = top_k if top_k is not None else default_top_k
    char_budget = char_budget if char_budget is not None else default_budget
    out = Path(out_dir) if out_dir else None

    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT paper_id, title, abstract, year, venue, source, doi FROM papers"
            " WHERE status = 'fetched' ORDER BY updated_at, paper_id LIMIT ?",
            (limit,),
        ).fetchall()
        papers = []
        items = []
        for row in rows:
            pid = row["paper_id"]
            merged = conn.execute(
                "SELECT merged_uses, disagreement, decision, priority FROM screening_merged"
                " WHERE paper_id = ?",
                (pid,),
            ).fetchone()
            confirmed = [
                dict(r)
                for r in conn.execute(
                    "SELECT use_type, target_section, confidence, needs_fulltext_confirm"
                    " FROM confirmed_uses WHERE paper_id = ?",
                    (pid,),
                )
            ]
            page_texts = _page_texts_for(conn, pid)
            selected = select.select_pages(
                page_texts,
                f"{row['title']} {row['abstract'] or ''}",
                top_k=top_k,
                char_budget=char_budget,
            )
            paper = {
                **dict(row),
                "merged_uses": json.loads(merged["merged_uses"]) if merged else [],
                "disagreement": json.loads(merged["disagreement"]) if merged else [],
                "confirmed_uses": confirmed,
                "pages": [{"page": n, "text": t} for n, t in selected],
            }
            if out is not None:
                path, size = write_payload(out, f"read-{safe_id(pid)}.json", paper)
                items.append(
                    {"paper_id": pid, "path": str(path), "pages": len(selected), "chars": size}
                )
            papers.append(paper)
        if out is not None:
            return common.ok(
                count=len(papers),
                item_count=len(items),
                items=items,
                top_k=top_k,
                char_budget=char_budget,
                out_dir=str(out),
            )
        return common.ok(count=len(papers), papers=papers, top_k=top_k, char_budget=char_budget)
    finally:
        conn.close()


def add_card(file: str | None) -> dict:
    labels = common.load_labels()
    fuzz_min = labels["thresholds"]["locator_fuzz_min"]
    cards = _as_cards(common.parse_json(common.read_input(file)))
    conn = db.connect()
    now = common.iso_now()
    inserted: list[str] = []
    try:
        with db.tx(conn):
            for card in cards:
                _validate_enums(labels, card)
                paper = conn.execute(
                    "SELECT paper_id, title, year, status FROM papers WHERE paper_id = ?",
                    (card.paper_id,),
                ).fetchone()
                if paper is None:
                    raise ValueError(f"unknown paper_id: {card.paper_id}")

                page_texts = _page_texts_for(conn, card.paper_id)
                parsed = locator.parse_pages(card.evidence_locator)
                status, ratio, matched, _page = locator.check_span(
                    card.source_span, page_texts, parsed, fuzz_min
                )
                if status != "verified":
                    raise locator.LocatorError(
                        f"locator failed for {card.paper_id} "
                        f"{card.use_type}/{card.target_section}: ratio={ratio:.1f} < {fuzz_min}"
                    )

                card_id = card.card_id or f"card-{uuid.uuid4().hex[:12]}"
                conn.execute(
                    """
                    INSERT INTO evidence_cards
                      (card_id, paper_id, use_type, target_section, claim, evidence_locator,
                       source_span, method_snippet, caveat, confidence, stage, locator_status,
                       original_prediction, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        card_id,
                        card.paper_id,
                        card.use_type,
                        card.target_section,
                        card.claim,
                        card.evidence_locator,
                        card.source_span,
                        card.method_snippet,
                        card.caveat,
                        card.confidence,
                        card.stage,
                        status,
                        json.dumps(card.original_prediction, ensure_ascii=False)
                        if card.original_prediction is not None
                        else None,
                        now,
                    ),
                )
                conn.execute(
                    "INSERT OR REPLACE INTO locator_checks (card_id, match_ratio, matched_text, checked_at)"
                    " VALUES (?, ?, ?, ?)",
                    (card_id, ratio, matched, now),
                )
                inserted.append(card_id)

                if paper["status"] == "fetched":
                    db.transition(conn, card.paper_id, "confirming", "add-card")

        return common.ok(count=len(inserted), card_ids=inserted)
    finally:
        conn.close()


def verify_card(card_id: str) -> dict:
    labels = common.load_labels()
    fuzz_min = labels["thresholds"]["locator_fuzz_min"]
    conn = db.connect()
    now = common.iso_now()
    try:
        with db.tx(conn):
            card = conn.execute(
                "SELECT * FROM evidence_cards WHERE card_id = ?", (card_id,)
            ).fetchone()
            if card is None:
                raise ValueError(f"unknown card_id: {card_id}")
            page_texts = _page_texts_for(conn, card["paper_id"])
            parsed = locator.parse_pages(card["evidence_locator"])
            status, ratio, matched, _page = locator.check_span(
                card["source_span"] or "", page_texts, parsed, fuzz_min
            )
            conn.execute(
                "UPDATE evidence_cards SET locator_status = ? WHERE card_id = ?",
                (status, card_id),
            )
            conn.execute(
                "INSERT OR REPLACE INTO locator_checks (card_id, match_ratio, matched_text, checked_at)"
                " VALUES (?, ?, ?, ?)",
                (card_id, ratio, matched, now),
            )
        return common.ok(card_id=card_id, locator_status=status, match_ratio=ratio, matched_text=matched)
    finally:
        conn.close()
