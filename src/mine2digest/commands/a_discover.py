"""阶段 A · 发现：add-candidates / add-relations。"""

from __future__ import annotations

import json
from typing import Iterator

from .. import common, db


def _iter_jsonl(text: str) -> Iterator[tuple[int, dict]]:
    for lineno, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"line {lineno}: invalid JSON: {exc}") from exc
        if not isinstance(rec, dict):
            raise ValueError(f"line {lineno}: expected a JSON object")
        yield lineno, rec


def _find_existing(conn, ids: list[tuple[str, str]]) -> str | None:
    for id_type, id_value in ids:
        row = conn.execute(
            "SELECT paper_id FROM paper_ids WHERE id_type = ? AND id_value = ?",
            (id_type, id_value),
        ).fetchone()
        if row:
            return row["paper_id"]
    return None


def _backfill(conn, paper_id: str, ids: list[tuple[str, str]], rec: dict, source: str | None) -> None:
    for id_type, id_value in ids:
        conn.execute(
            "INSERT OR IGNORE INTO paper_ids (paper_id, id_type, id_value) VALUES (?, ?, ?)",
            (paper_id, id_type, id_value),
        )
    row = conn.execute("SELECT doi, source FROM papers WHERE paper_id = ?", (paper_id,)).fetchone()
    if row is None:
        return
    if not row["doi"]:
        doi = common.normalize_doi(rec.get("doi"))
        if doi:
            conn.execute("UPDATE papers SET doi = ? WHERE paper_id = ?", (doi, paper_id))
    if not row["source"]:
        src = rec.get("source") or source
        if src:
            conn.execute("UPDATE papers SET source = ? WHERE paper_id = ?", (src, paper_id))


def add_candidates(file: str | None, source: str | None) -> dict:
    text = common.read_input(file)
    conn = db.connect()
    now = common.iso_now()
    stats = {"inserted": 0, "updated": 0, "skipped": 0}
    try:
        with db.tx(conn):
            for lineno, rec in _iter_jsonl(text):
                title = (rec.get("title") or "").strip()
                if not title:
                    raise ValueError(f"line {lineno}: missing title")
                ids = common.candidate_ids(rec)
                if not ids:
                    stats["skipped"] += 1
                    continue

                existing = _find_existing(conn, ids)
                if existing is None:
                    pid = common.canonical_paper_id(rec)
                    if conn.execute(
                        "SELECT 1 FROM papers WHERE paper_id = ?", (pid,)
                    ).fetchone():
                        existing = pid

                if existing:
                    _backfill(conn, existing, ids, rec, source)
                    conn.execute(
                        "UPDATE papers SET updated_at = ? WHERE paper_id = ?", (now, existing)
                    )
                    stats["updated"] += 1
                else:
                    pid = common.canonical_paper_id(rec)
                    conn.execute(
                        """
                        INSERT INTO papers
                          (paper_id, doi, title, abstract, year, venue, source, status, added_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, 'candidate', ?, ?)
                        """,
                        (
                            pid,
                            common.normalize_doi(rec.get("doi")),
                            title,
                            rec.get("abstract"),
                            rec.get("year"),
                            rec.get("venue"),
                            rec.get("source") or source,
                            now,
                            now,
                        ),
                    )
                    _backfill(conn, pid, ids, rec, source)
                    stats["inserted"] += 1
        return common.ok(**stats)
    finally:
        conn.close()


def add_relations(file: str | None) -> dict:
    text = common.read_input(file)
    conn = db.connect()
    upserted = 0
    try:
        with db.tx(conn):
            for lineno, rec in _iter_jsonl(text):
                src = rec.get("src_id")
                dst = rec.get("dst_id")
                relation = rec.get("relation")
                if not (src and dst and relation):
                    raise ValueError(
                        f"line {lineno}: missing src_id/dst_id/relation"
                    )
                conn.execute(
                    """
                    INSERT OR REPLACE INTO relations
                      (src_id, dst_id, relation, is_influential, contexts)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        src,
                        dst,
                        relation,
                        1 if rec.get("is_influential") else 0,
                        json.dumps(rec.get("contexts") or [], ensure_ascii=False),
                    ),
                )
                upserted += 1
        return common.ok(upserted=upserted)
    finally:
        conn.close()
