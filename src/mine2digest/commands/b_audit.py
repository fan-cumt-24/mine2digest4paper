"""阶段 B · 反证与审计：next-caveat / add-caveat / audit / submit-audit / reject-card。"""

from __future__ import annotations

from pathlib import Path

from .. import audit, common, db
from ..models import AuditFile, AuditResult, Caveat
from ..pdf import locator, select
from .b_read import _page_texts_for, safe_id, write_payload


def next_caveat(
    limit: int,
    out_dir: str | Path | None = None,
    char_budget: int | None = None,
    top_k: int | None = None,
) -> dict:
    """取 caveat IS NULL 且 fulltext_confirmed 的卡，带**按论文去重且预选**的页文本。"""
    labels = common.load_labels()
    default_top_k, default_budget = select.resolve_budget(labels, "caveat")
    top_k = top_k if top_k is not None else default_top_k
    char_budget = char_budget if char_budget is not None else default_budget
    out = Path(out_dir) if out_dir else None

    conn = db.connect()
    try:
        rows = conn.execute(
            """
            SELECT c.*, p.title, p.abstract, p.year, p.venue
            FROM evidence_cards c JOIN papers p ON p.paper_id = c.paper_id
            WHERE c.caveat IS NULL AND c.stage = 'fulltext_confirmed'
            ORDER BY c.created_at, c.card_id
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        grouped: dict[str, list[dict]] = {}
        for row in rows:
            grouped.setdefault(row["paper_id"], []).append(dict(row))

        papers = []
        cards: list[dict] = []
        items: list[dict] = []
        for pid, group in grouped.items():
            page_texts = _page_texts_for(conn, pid)
            pin = {p for r in group for p in locator.parse_pages(r.get("evidence_locator"))}
            query = " ".join(
                [group[0].get("title") or "", group[0].get("abstract") or ""]
                + [f"{r.get('claim') or ''} {r.get('source_span') or ''}" for r in group]
            )
            selected = select.select_pages(
                page_texts, query, top_k=top_k, char_budget=char_budget, pin=pin
            )
            pages = [{"page": n, "text": t} for n, t in selected]
            paper = {"paper_id": pid, "title": group[0].get("title"), "pages": pages}
            papers.append(paper)
            paper_cards = []
            for row in group:
                card = {k: v for k, v in row.items() if k != "abstract"}
                card["paper_id"] = pid
                paper_cards.append(card)
                cards.append(card)
            if out is not None:
                payload = {**paper, "venue": group[0].get("venue"), "cards": paper_cards}
                path, _size = write_payload(out, f"caveat-{safe_id(pid)}.json", payload)
                for row in group:
                    items.append({"card_id": row["card_id"], "paper_id": pid, "path": str(path)})

        if out is not None:
            return common.ok(
                count=len(cards),
                item_count=len(items),
                items=items,
                top_k=top_k,
                char_budget=char_budget,
                out_dir=str(out),
            )
        return common.ok(count=len(cards), papers=papers, cards=cards, top_k=top_k, char_budget=char_budget)
    finally:
        conn.close()


def add_caveat(card_id: str, file: str | None) -> dict:
    payload = common.parse_json(common.read_input(file))
    caveat = Caveat(**payload) if isinstance(payload, dict) else None
    if caveat is None or not caveat.caveat.strip():
        raise ValueError("caveat text is required")
    conn = db.connect()
    try:
        with db.tx(conn):
            row = conn.execute(
                "SELECT card_id, confidence FROM evidence_cards WHERE card_id = ?", (card_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"unknown card_id: {card_id}")
            confidence = caveat.confidence if caveat.confidence is not None else row["confidence"]
            conn.execute(
                "UPDATE evidence_cards SET caveat = ?, confidence = ? WHERE card_id = ?",
                (caveat.caveat, confidence, card_id),
            )
        return common.ok(card_id=card_id, confidence=confidence)
    finally:
        conn.close()


def run_audit(rate: float | None) -> dict:
    labels = common.load_labels()
    thresholds = labels["thresholds"]
    rate = rate if rate is not None else thresholds["audit_rate"]
    conn = db.connect()
    now = common.iso_now()
    try:
        with db.tx(conn):
            rows = audit.sample_cards(conn, rate, now)
            cards = []
            for row in rows:
                card = conn.execute(
                    """
                    SELECT c.card_id, c.paper_id, c.use_type, c.target_section, c.claim,
                           c.source_span, c.confidence, p.title, p.source
                    FROM evidence_cards c JOIN papers p ON p.paper_id = c.paper_id
                    WHERE c.card_id = ?
                    """,
                    (row["card_id"],),
                ).fetchone()
                cards.append(dict(card))
        return common.ok(sampled=len(cards), rate=rate, cards=cards)
    finally:
        conn.close()


def submit_audit(file: str | None) -> dict:
    payload = common.parse_json(common.read_input(file))
    if isinstance(payload, dict) and "results" in payload:
        results = AuditFile(**payload).results
    elif isinstance(payload, list):
        results = [AuditResult(**item) for item in payload]
    elif isinstance(payload, dict):
        results = [AuditResult(**payload)]
    else:
        raise ValueError("unrecognized audit payload")

    labels = common.load_labels()
    thresholds = labels["thresholds"]
    conn = db.connect()
    now = common.iso_now()
    try:
        with db.tx(conn):
            updated = 0
            for result in results:
                cur = conn.execute(
                    "SELECT card_id FROM audit_samples WHERE card_id = ?", (result.card_id,)
                ).fetchone()
                if cur is None:
                    continue
                conn.execute(
                    "UPDATE audit_samples SET passes = ?, reason = ? WHERE card_id = ?",
                    (1 if result.passes else 0, result.reason, result.card_id),
                )
                updated += 1
            reliability = audit.update_source_reliability(
                conn,
                thresholds["audit_window"],
                thresholds["audit_pass_min"],
                0.5,
                now,
            )
        return common.ok(updated=updated, reliability=reliability)
    finally:
        conn.close()


def reject_card(card_id: str, reason: str | None) -> dict:
    conn = db.connect()
    try:
        with db.tx(conn):
            cursor = conn.execute(
                "UPDATE evidence_cards SET stage = 'rejected', reject_reason = ? WHERE card_id = ?",
                (reason, card_id),
            )
            if cursor.rowcount == 0:
                raise ValueError(f"unknown card_id: {card_id}")
        return common.ok(card_id=card_id, stage="rejected")
    finally:
        conn.close()
