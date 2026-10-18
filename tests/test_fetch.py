"""阶段 A 获取：next-fetch / report-fetch / ingest-manual。"""

import json
from pathlib import Path

from conftest import add_paper
from typer.testing import CliRunner

from mine2digest import db
from mine2digest.cli import app
from mine2digest.common import iso_now

runner = CliRunner()


def invoke(args, input_text=None):
    return runner.invoke(app, args, input=input_text)


def body(result):
    return json.loads(result.stdout)


def seed_queued(conn, pid, priority=1.0, title=None):
    add_paper(conn, pid, title=title or f"Paper {pid}")
    conn.execute(
        "INSERT INTO fetch_queue (paper_id, priority, enqueued_at) VALUES (?, ?, ?)",
        (pid, priority, iso_now()),
    )
    db.transition(conn, pid, "screened", "test")
    db.transition(conn, pid, "queued", "test")


def test_next_fetch_claims_in_priority_order(isolated, conn):
    seed_queued(conn, "p1", 1.0)
    seed_queued(conn, "p2", 2.0)

    result = body(invoke(["next-fetch", "--limit", "5", "--worker", "w1"]))
    assert result["count"] == 2
    assert [p["paper_id"] for p in result["papers"]] == ["p2", "p1"]

    rows = conn.execute("SELECT paper_id, status FROM papers ORDER BY paper_id").fetchall()
    assert {r["paper_id"]: r["status"] for r in rows} == {"p1": "fetching", "p2": "fetching"}
    assert conn.execute("SELECT DISTINCT claimed_by FROM fetch_queue").fetchone()["claimed_by"] == "w1"

    # 已认领的不会被再次认领
    assert body(invoke(["next-fetch", "--limit", "5"]))["count"] == 0


def test_next_fetch_returns_ids_and_attempts(isolated, conn):
    seed_queued(conn, "p1")
    conn.execute("INSERT INTO paper_ids (paper_id, id_type, id_value) VALUES ('p1','arxiv','2401.1')")
    conn.execute("INSERT INTO paper_ids (paper_id, id_type, id_value) VALUES ('p1','doi','10.1/x')")
    conn.execute(
        "INSERT INTO fetch_attempts (paper_id, source, success, attempted_at) VALUES ('p1','unpaywall',0,?)",
        (iso_now(),),
    )

    paper = body(invoke(["next-fetch"]))["papers"][0]
    assert paper["ids"] == {"arxiv": "2401.1", "doi": "10.1/x"}
    assert paper["attempted_sources"] == ["unpaywall"]


def test_report_fetch_success_archives(isolated, conn):
    seed_queued(conn, "p1", title="A Great Paper")
    invoke(["next-fetch"])

    pdf = Path("inbox") / "p1.pdf"
    pdf.parent.mkdir(parents=True, exist_ok=True)
    pdf.write_bytes(b"%PDF-1.4 fake")

    result = body(invoke(
        ["report-fetch", "--paper-id", "p1", "--status", "success",
         "--path", str(pdf), "--source", "arxiv"]
    ))
    assert result["ok"] is True
    assert result["status"] == "fetched"

    dest = Path(result["path"])
    assert dest.exists() and dest.name == "paper.pdf"
    assert not pdf.exists()  # 已移动
    meta = json.loads((dest.parent / "meta.json").read_text(encoding="utf-8"))
    assert meta["paper_id"] == "p1"
    assert meta["source"] == "arxiv"

    assert conn.execute("SELECT status FROM papers WHERE paper_id='p1'").fetchone()["status"] == "fetched"
    assert conn.execute("SELECT COUNT(*) c FROM fetch_queue WHERE paper_id='p1'").fetchone()["c"] == 0


def test_report_fetch_failed_without_retry(isolated, conn):
    seed_queued(conn, "p1")
    invoke(["next-fetch"])

    result = body(invoke(
        ["report-fetch", "--paper-id", "p1", "--status", "failed",
         "--source", "unpaywall", "--error", "404"]
    ))
    assert result["status"] == "fetch_failed"
    assert conn.execute("SELECT status FROM papers WHERE paper_id='p1'").fetchone()["status"] == "fetch_failed"
    # 未重试：仍被认领（claimed_at 非空），不会被再次认领
    assert conn.execute("SELECT claimed_at FROM fetch_queue WHERE paper_id='p1'").fetchone()["claimed_at"] is not None
    assert body(invoke(["next-fetch"]))["count"] == 0

    fail_list = json.loads(Path("exports/fail_list.json").read_text(encoding="utf-8"))
    assert fail_list[0]["paper_id"] == "p1"
    assert fail_list[0]["attempts"][0]["error"] == "404"


def test_report_fetch_failed_with_retry(isolated, conn):
    seed_queued(conn, "p1")
    invoke(["next-fetch"])

    result = body(invoke(
        ["report-fetch", "--paper-id", "p1", "--status", "failed",
         "--source", "pmc", "--error", "timeout", "--retry"]
    ))
    assert result["status"] == "queued"
    assert conn.execute("SELECT status FROM papers WHERE paper_id='p1'").fetchone()["status"] == "queued"
    assert conn.execute("SELECT claimed_at FROM fetch_queue WHERE paper_id='p1'").fetchone()["claimed_at"] is None
    # 可再次认领
    assert body(invoke(["next-fetch"]))["count"] == 1


def test_report_fetch_success_requires_existing_file(isolated, conn):
    seed_queued(conn, "p1")
    invoke(["next-fetch"])
    result = invoke(
        ["report-fetch", "--paper-id", "p1", "--status", "success", "--path", "nope.pdf", "--source", "arxiv"]
    )
    assert result.exit_code == 1
    assert "file not found" in body(result)["reason"]


def test_ingest_manual_creates_new_paper(isolated, conn):
    pdf = Path("inbox") / "manual.pdf"
    pdf.parent.mkdir(parents=True, exist_ok=True)
    pdf.write_bytes(b"%PDF-1.4 manual")

    result = body(invoke(
        ["ingest-manual", "--file", str(pdf), "--doi", "10.1/manual", "--title", "Manual Paper"]
    ))
    assert result["ok"] is True
    paper_id = result["paper_id"]
    assert conn.execute("SELECT status FROM papers WHERE paper_id=?", (paper_id,)).fetchone()["status"] == "fetched"
    dest = Path(result["path"])
    assert dest.exists()
    ids = {(r["id_type"], r["id_value"]) for r in conn.execute("SELECT id_type, id_value FROM paper_ids")}
    assert ("doi", "10.1/manual") in ids


def test_ingest_manual_attaches_to_existing_failed_paper(isolated, conn):
    add_paper(conn, "seed", title="Closed Paper", status="fetch_failed")
    conn.execute("INSERT INTO paper_ids (paper_id, id_type, id_value) VALUES ('seed','doi','10.1/closed')")

    pdf = Path("inbox") / "closed.pdf"
    pdf.parent.mkdir(parents=True, exist_ok=True)
    pdf.write_bytes(b"%PDF-1.4 closed")

    result = body(invoke(["ingest-manual", "--file", str(pdf), "--doi", "10.1/closed"]))
    assert result["paper_id"] == "seed"
    assert conn.execute("SELECT status FROM papers WHERE paper_id='seed'").fetchone()["status"] == "fetched"


def test_ingest_manual_missing_file_rejected(isolated):
    result = invoke(["ingest-manual", "--file", "inbox/nope.pdf", "--title", "T"])
    assert result.exit_code == 1
    assert "inbox file not found" in body(result)["reason"]
