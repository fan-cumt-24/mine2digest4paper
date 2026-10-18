"""阶段 A · 获取：next-fetch / report-fetch / ingest-manual。"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .. import common, db, store

FAIL_LIST = "exports/fail_list.json"


def fail_list_path() -> Path:
    return common.wpath("exports", "fail_list.json")


def next_fetch(limit: int, worker: str | None = None) -> dict:
    """原子认领 fetch_queue 中未认领的论文（按 priority 降序），置为 fetching。"""
    conn = db.connect()
    now = common.iso_now()
    worker = worker or f"worker-{os.getpid()}"
    try:
        with db.tx(conn):
            claimed = conn.execute(
                """
                UPDATE fetch_queue
                SET claimed_at = ?, claimed_by = ?
                WHERE paper_id IN (
                    SELECT paper_id FROM fetch_queue
                    WHERE claimed_at IS NULL
                    ORDER BY priority DESC, enqueued_at, paper_id
                    LIMIT ?
                )
                RETURNING paper_id, priority, enqueued_at
                """,
                (now, worker, limit),
            ).fetchall()
            # RETURNING 不保证顺序，这里显式按 priority 降序稳定排序
            claimed = sorted(
                claimed,
                key=lambda r: (-r["priority"], r["enqueued_at"] or "", r["paper_id"]),
            )

            papers = []
            for row in claimed:
                pid = row["paper_id"]
                paper = conn.execute(
                    "SELECT paper_id, doi, title, abstract, year, venue, source"
                    " FROM papers WHERE paper_id = ?",
                    (pid,),
                ).fetchone()
                ids = {
                    r["id_type"]: r["id_value"]
                    for r in conn.execute(
                        "SELECT id_type, id_value FROM paper_ids WHERE paper_id = ?", (pid,)
                    )
                }
                attempted = [
                    r["source"]
                    for r in conn.execute(
                        "SELECT DISTINCT source FROM fetch_attempts"
                        " WHERE paper_id = ? AND success = 0 AND source IS NOT NULL",
                        (pid,),
                    )
                ]
                db.transition(conn, pid, "fetching", "next-fetch")
                papers.append(
                    {
                        **dict(paper),
                        "status": "fetching",
                        "priority": row["priority"],
                        "ids": ids,
                        "attempted_sources": attempted,
                    }
                )
        return common.ok(worker=worker, count=len(papers), papers=papers)
    finally:
        conn.close()


def _find_paper(conn, ids: list[tuple[str, str]]) -> str | None:
    for id_type, id_value in ids:
        row = conn.execute(
            "SELECT paper_id FROM paper_ids WHERE id_type = ? AND id_value = ?",
            (id_type, id_value),
        ).fetchone()
        if row:
            return row["paper_id"]
    return None


def _write_fail_list(conn) -> None:
    rows = conn.execute(
        "SELECT paper_id, doi, title, year, venue, source FROM papers"
        " WHERE status = 'fetch_failed' ORDER BY paper_id"
    ).fetchall()
    items = []
    for row in rows:
        attempts = [
            dict(a)
            for a in conn.execute(
                "SELECT source, url, error, attempted_at FROM fetch_attempts WHERE paper_id = ?",
                (row["paper_id"],),
            )
        ]
        items.append({**dict(row), "attempts": attempts})
    path = fail_list_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def report_fetch(
    paper_id: str,
    status: str,
    path: str | None = None,
    source: str | None = None,
    error: str | None = None,
    retry: bool = False,
    version: str = "preprint",
) -> dict:
    conn = db.connect()
    now = common.iso_now()
    try:
        with db.tx(conn):
            paper = conn.execute(
                "SELECT paper_id, doi, title, year, source FROM papers WHERE paper_id = ?",
                (paper_id,),
            ).fetchone()
            if paper is None:
                raise ValueError(f"unknown paper_id: {paper_id}")

            if status == "success":
                if not path:
                    raise ValueError("status=success requires --path")
                src = Path(path)
                if not src.exists():
                    raise ValueError(f"file not found: {path}")
                slug = store.slugify(paper_id, paper["title"], paper["year"])
                dest = store.archive_pdf(slug, src)
                store.write_meta(
                    slug,
                    {
                        "paper_id": paper_id,
                        "doi": paper["doi"],
                        "title": paper["title"],
                        "source": source or paper["source"],
                        "source_url": source,
                        "version": version,
                        "fetched_at": now,
                        "slug": slug,
                    },
                )
                conn.execute(
                    "INSERT INTO fetch_attempts (paper_id, source, url, success, error, attempted_at)"
                    " VALUES (?, ?, ?, 1, NULL, ?)",
                    (paper_id, source, str(dest), now),
                )
                db.transition(conn, paper_id, "fetched", "report-fetch")
                conn.execute("DELETE FROM fetch_queue WHERE paper_id = ?", (paper_id,))
                return common.ok(paper_id=paper_id, status="fetched", path=str(dest))

            if status == "failed":
                conn.execute(
                    "INSERT INTO fetch_attempts (paper_id, source, url, success, error, attempted_at)"
                    " VALUES (?, ?, ?, 0, ?, ?)",
                    (paper_id, source, path, error, now),
                )
                db.transition(conn, paper_id, "fetch_failed", "report-fetch")
                if retry:
                    conn.execute(
                        "UPDATE fetch_queue SET claimed_at = NULL, claimed_by = NULL"
                        " WHERE paper_id = ?",
                        (paper_id,),
                    )
                    db.transition(conn, paper_id, "queued", "report-fetch")
                _write_fail_list(conn)
                return common.ok(
                    paper_id=paper_id,
                    status="queued" if retry else "fetch_failed",
                    retry=bool(retry),
                )

            raise ValueError(f"invalid status: {status!r} (expected 'success' or 'failed')")
    finally:
        conn.close()


def ingest_manual(
    file: str,
    doi: str | None = None,
    title: str | None = None,
    version: str = "preprint",
    slug: str | None = None,
) -> dict:
    """从 inbox 投放闭源 PDF：匹配已有论文或新建，归档并置为 fetched。"""
    src = Path(file)
    if not src.exists():
        raise ValueError(f"inbox file not found: {file}")
    conn = db.connect()
    now = common.iso_now()
    try:
        with db.tx(conn):
            ids = common.candidate_ids({"doi": doi, "title": title})
            paper_id = _find_paper(conn, ids)

            if paper_id is None:
                paper_id = common.canonical_paper_id({"doi": doi, "title": title or src.stem})
                row = conn.execute(
                    "SELECT title, year FROM papers WHERE paper_id = ?", (paper_id,)
                ).fetchone()
                if row is None:
                    conn.execute(
                        "INSERT INTO papers (paper_id, doi, title, status, source, added_at, updated_at)"
                        " VALUES (?, ?, ?, 'fetched', 'manual', ?, ?)",
                        (paper_id, common.normalize_doi(doi), title or src.stem, now, now),
                    )
                    for id_type, id_value in ids:
                        conn.execute(
                            "INSERT OR IGNORE INTO paper_ids (paper_id, id_type, id_value)"
                            " VALUES (?, ?, ?)",
                            (paper_id, id_type, id_value),
                        )
            paper = conn.execute(
                "SELECT title, year, status FROM papers WHERE paper_id = ?", (paper_id,)
            ).fetchone()
            if paper["status"] != "fetched":
                db.transition(conn, paper_id, "fetched", "ingest-manual")

            slug_name = store.slugify(paper_id, paper["title"], paper["year"], override=slug)
            dest = store.archive_pdf(slug_name, src)
            store.write_meta(
                slug_name,
                {
                    "paper_id": paper_id,
                    "doi": common.normalize_doi(doi),
                    "title": paper["title"],
                    "source": "manual",
                    "version": version,
                    "fetched_at": now,
                    "slug": slug_name,
                },
            )
            conn.execute(
                "INSERT INTO fetch_attempts (paper_id, source, url, success, error, attempted_at)"
                " VALUES (?, 'manual', ?, 1, NULL, ?)",
                (paper_id, str(dest), now),
            )
            conn.execute("DELETE FROM fetch_queue WHERE paper_id = ?", (paper_id,))
        return common.ok(paper_id=paper_id, status="fetched", path=str(dest))
    finally:
        conn.close()
