"""阶段 A 发现：add-candidates / add-relations 及去重逻辑。"""

import json

from typer.testing import CliRunner

from mine2digest.cli import app
from mine2digest.common import candidate_ids, canonical_paper_id, reconstruct_abstract

runner = CliRunner()


def invoke(args, input_text=None):
    return runner.invoke(app, args, input=input_text)


def body(result):
    return json.loads(result.stdout)


def jsonl(*records):
    return "\n".join(json.dumps(r) for r in records)


# --- common 纯函数 ---


def test_canonical_paper_id_priority():
    assert canonical_paper_id({"paper_id": "X", "openalex_id": "W1"}) == "X"
    assert canonical_paper_id({"openalex_id": "W1", "s2_id": "S1"}) == "W1"
    assert canonical_paper_id({"s2_id": "S1", "doi": "10.1/x"}) == "S1"
    assert canonical_paper_id({"doi": "https://doi.org/10.1/x"}) == "doi:10.1/x"
    assert canonical_paper_id({"title": "Hello World"}).startswith("local:")


def test_candidate_ids_extracts_and_normalizes():
    ids = dict(candidate_ids({"title": "Hi", "doi": "doi:10.1/X", "openalex_id": "W1", "s2_id": "S1"}))
    assert ids["doi"] == "10.1/x"
    assert ids["openalex"] == "W1"
    assert ids["s2"] == "S1"
    assert "title" in ids


def test_reconstruct_abstract():
    assert reconstruct_abstract(None) is None
    assert reconstruct_abstract({"We": [0], "build": [1], "X": [2]}) == "We build X"


# --- add-candidates ---


def test_add_candidates_insert(isolated, conn):
    rec = {"title": "Paper A", "openalex_id": "W1", "doi": "10.1/a", "year": 2020, "venue": "NeurIPS"}
    result = body(invoke(["add-candidates", "--source", "openalex"], input_text=jsonl(rec)))
    assert result == {"ok": True, "inserted": 1, "updated": 0, "skipped": 0}

    row = conn.execute("SELECT paper_id, title, status, source FROM papers").fetchone()
    assert row["paper_id"] == "W1"
    assert row["status"] == "candidate"
    assert row["source"] == "openalex"
    ids = {(r["id_type"], r["id_value"]) for r in conn.execute("SELECT id_type, id_value FROM paper_ids")}
    assert ("openalex", "W1") in ids
    assert ("doi", "10.1/a") in ids


def test_add_candidates_dedup_by_doi(isolated, conn):
    invoke(["add-candidates"], input_text=jsonl({"title": "T", "doi": "10.1/x", "openalex_id": "W1"}))
    result = body(invoke(["add-candidates"], input_text=jsonl({"title": "Different title", "doi": "10.1/x"})))
    assert result["inserted"] == 0
    assert result["updated"] == 1
    assert conn.execute("SELECT COUNT(*) c FROM papers").fetchone()["c"] == 1


def test_add_candidates_dedup_by_title_fallback(isolated, conn):
    invoke(["add-candidates"], input_text=jsonl({"title": "Same Title", "s2_id": "S1"}))
    result = body(invoke(["add-candidates"], input_text=jsonl({"title": "  same   title  "})))
    assert result["inserted"] == 0
    assert result["updated"] == 1
    assert conn.execute("SELECT COUNT(*) c FROM papers").fetchone()["c"] == 1


def test_add_candidates_is_idempotent_and_preserves_status(isolated, conn):
    rec = json.dumps({"title": "T", "openalex_id": "W1"})
    invoke(["add-candidates"], input_text=rec)
    conn.execute("UPDATE papers SET status='queued' WHERE paper_id='W1'")

    result = body(invoke(["add-candidates"], input_text=rec))
    assert result["updated"] == 1
    assert conn.execute("SELECT status FROM papers WHERE paper_id='W1'").fetchone()["status"] == "queued"
    assert conn.execute("SELECT COUNT(*) c FROM papers").fetchone()["c"] == 1


def test_add_candidates_backfills_ids_and_source(isolated, conn):
    invoke(["add-candidates"], input_text=jsonl({"title": "T", "doi": "10.1/x"}))
    invoke(["add-candidates", "--source", "s2"], input_text=jsonl({"title": "T", "openalex_id": "W9"}))

    ids = {(r["id_type"], r["id_value"]) for r in conn.execute("SELECT id_type, id_value FROM paper_ids")}
    assert ("openalex", "W9") in ids
    assert conn.execute("SELECT COUNT(*) c FROM papers").fetchone()["c"] == 1
    assert conn.execute("SELECT source FROM papers").fetchone()["source"] == "s2"


def test_add_candidates_missing_title_rejected(isolated):
    result = invoke(["add-candidates"], input_text=jsonl({"doi": "10.1/x"}))
    assert result.exit_code == 1
    assert "missing title" in body(result)["reason"]


# --- add-relations ---


def test_add_relations_upsert_and_replace(isolated, conn):
    invoke(["add-candidates"], input_text=jsonl(
        {"title": "A", "openalex_id": "W1"},
        {"title": "B", "openalex_id": "W2"},
    ))
    invoke(["add-relations"], input_text=jsonl(
        {"src_id": "W2", "dst_id": "W1", "relation": "cites", "is_influential": True, "contexts": ["c1"]}
    ))
    # 覆盖同一条边
    result = body(invoke(["add-relations"], input_text=jsonl(
        {"src_id": "W2", "dst_id": "W1", "relation": "cites", "is_influential": False, "contexts": ["c2"]}
    )))
    assert result["upserted"] == 1
    rows = conn.execute("SELECT is_influential, contexts FROM relations").fetchall()
    assert len(rows) == 1
    assert rows[0]["is_influential"] == 0
    assert json.loads(rows[0]["contexts"]) == ["c2"]


def test_add_relations_missing_field_rejected(isolated, conn):
    result = invoke(["add-relations"], input_text=jsonl({"src_id": "W1", "relation": "cites"}))
    assert result.exit_code == 1
    assert "missing src_id/dst_id/relation" in body(result)["reason"]


# --- 打通 A → B 的输入 ---


def test_next_candidates_includes_citation_contexts(isolated, conn):
    invoke(["add-candidates"], input_text=jsonl(
        {"title": "Seed", "openalex_id": "W1"},
        {"title": "Cited", "openalex_id": "W2"},
    ))
    invoke(["add-relations"], input_text=jsonl(
        {"src_id": "W1", "dst_id": "W2", "relation": "cites", "contexts": ["we extend W2"]}
    ))

    result = body(invoke(["next-candidates", "--agent-id", "A"]))
    by_id = {c["paper_id"]: c for c in result["candidates"]}
    assert by_id["W2"]["citation_contexts"] == [{"src_id": "W1", "contexts": ["we extend W2"]}]
    assert by_id["W1"]["citation_contexts"] == []
