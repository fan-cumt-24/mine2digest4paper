"""阶段 C：coverage / query / lookup / export / revive。"""

import json
from pathlib import Path

from conftest import add_paper
from typer.testing import CliRunner

from mine2digest.cli import app
from mine2digest.common import iso_now

runner = CliRunner()


def invoke(args, input_text=None):
    return runner.invoke(app, args, input=input_text)


def body(result):
    return json.loads(result.stdout)


def add_card(conn, card_id, paper_id="p1", use_type="baseline", section="experiments",
             confidence=0.9, stage="fulltext_confirmed"):
    conn.execute(
        "INSERT INTO evidence_cards"
        " (card_id, paper_id, use_type, target_section, claim, source_span, confidence,"
        "  stage, locator_status, created_at)"
        " VALUES (?, ?, ?, ?, 'claim', 'span', ?, ?, 'verified', ?)",
        (card_id, paper_id, use_type, section, confidence, stage, iso_now()),
    )


def add_merged(conn, pid, uses, disagreement=None):
    conn.execute(
        "INSERT INTO screening_merged (paper_id, merged_uses, decision, priority, disagreement, merged_at)"
        " VALUES (?, ?, 'low', 0.0, ?, ?)",
        (pid, json.dumps(uses), json.dumps(disagreement or []), iso_now()),
    )


def test_coverage_includes_background_and_counts_cards(isolated, conn):
    add_paper(conn, "p1", status="confirmed")
    add_card(conn, "c1")
    matrix = body(invoke(["coverage"]))["matrix"]
    assert "background" in matrix["experiments"]
    assert matrix["experiments"]["background"]["n"] == 0
    assert matrix["experiments"]["baseline"]["n"] == 1
    assert matrix["experiments"]["baseline"]["card_ids"] == ["c1"]
    assert matrix["intro"]["support_claim"]["reclaimable"] is False


def test_coverage_marks_reclaimable_from_low_priority(isolated, conn):
    add_paper(conn, "p1", status="low_priority")
    add_merged(conn, "p1", [{"type": "support_claim", "target_section": "intro"}])
    matrix = body(invoke(["coverage", "--section", "intro"]))["matrix"]
    assert list(matrix) == ["intro"]
    cell = matrix["intro"]["support_claim"]
    assert cell["n"] == 0
    assert cell["reclaimable"] is True
    assert cell["reclaimable_papers"] == ["p1"]


def test_query_orders_by_confidence_times_source_weight(isolated, conn):
    add_paper(conn, "p1")
    add_paper(conn, "p2")
    conn.execute("UPDATE papers SET source='arxiv' WHERE paper_id='p1'")
    conn.execute("UPDATE papers SET source='pmc' WHERE paper_id='p2'")
    conn.execute("INSERT INTO source_reliability (source, weight, updated_at) VALUES ('arxiv', 0.9, ?)", (iso_now(),))
    conn.execute("INSERT INTO source_reliability (source, weight, updated_at) VALUES ('pmc', 1.0, ?)", (iso_now(),))
    add_card(conn, "c-low", paper_id="p2", confidence=0.8)   # 0.8 * 1.0 = 0.8
    add_card(conn, "c-high", paper_id="p1", confidence=1.0)  # 1.0 * 0.9 = 0.9
    add_card(conn, "c-rejected", paper_id="p1", confidence=1.0, stage="rejected")
    result = body(invoke(["query", "--type", "baseline", "--section", "experiments"]))
    assert [c["card_id"] for c in result["cards"]] == ["c-high", "c-low"]
    assert result["cards"][0]["source_weight"] == 0.9


def test_query_min_confidence_filters(isolated, conn):
    add_paper(conn, "p1")
    add_card(conn, "c1", confidence=0.3)
    add_card(conn, "c2", confidence=0.95)
    result = body(invoke(["query", "--type", "baseline", "--section", "experiments", "--min-confidence", "0.5"]))
    assert [c["card_id"] for c in result["cards"]] == ["c2"]


def test_lookup_returns_full_trace(isolated, conn):
    add_paper(conn, "p1", status="low_priority")
    conn.execute("INSERT INTO paper_ids (paper_id, id_type, id_value) VALUES ('p1', 'doi', '10.1/x')")
    add_merged(conn, "p1", [{"type": "baseline", "target_section": "experiments"}])
    add_card(conn, "c1")
    result = body(invoke(["lookup", "--paper-id", "p1"]))
    assert result["paper"]["paper_id"] == "p1"
    assert result["ids"] == [{"id_type": "doi", "id_value": "10.1/x"}]
    assert result["cards"][0]["card_id"] == "c1"
    assert "revivals" in result

    unknown = invoke(["lookup", "--paper-id", "nope"])
    assert unknown.exit_code == 1
    assert "unknown paper_id" in body(unknown)["reason"]


def test_export_all_and_single_view(isolated, conn):
    add_paper(conn, "p1", status="low_priority")
    add_card(conn, "c1")
    all_result = body(invoke(["export"]))
    assert "coverage_matrix.json" in all_result["exports"]
    assert (Path("exports") / "evidence_cards.jsonl").exists()
    assert (Path("exports") / "coverage_matrix.json").exists()

    single = body(invoke(["export", "--view", "fail_list"]))
    assert single["exports"] == ["fail_list.json"]
    bad = invoke(["export", "--view", "nonsense"])
    assert bad.exit_code == 1


def test_revive_by_cell_enqueues_discounted(isolated, conn):
    add_paper(conn, "p1", status="low_priority")
    add_merged(conn, "p1", [{"type": "baseline", "target_section": "experiments"}])
    result = body(invoke(["revive", "--type", "baseline", "--section", "experiments"]))
    assert result["revived"] == 1
    assert result["paper_ids"] == ["p1"]
    assert conn.execute("SELECT status FROM papers WHERE paper_id='p1'").fetchone()[0] == "queued"
    row = conn.execute("SELECT priority FROM fetch_queue WHERE paper_id='p1'").fetchone()
    assert row["priority"] == 0.5  # baseline 1.0 * discount 0.5
    assert conn.execute("SELECT revival_rounds FROM papers WHERE paper_id='p1'").fetchone()[0] == 1
    log = conn.execute("SELECT from_status, trigger_cell, round FROM revival_log").fetchone()
    assert (log["from_status"], log["trigger_cell"], log["round"]) == ("low_priority", "experiments/baseline", 1)


def test_revive_gaps_picks_reclaimable_cells(isolated, conn):
    add_paper(conn, "p1", status="low_priority")
    add_merged(conn, "p1", [{"type": "gap_evidence", "target_section": "related_work"}])
    result = body(invoke(["revive", "--gaps"]))
    assert result["revived"] == 1
    assert conn.execute("SELECT status FROM papers WHERE paper_id='p1'").fetchone()[0] == "queued"


def test_revive_gaps_skips_background_only(isolated, conn):
    add_paper(conn, "p1", status="low_priority")
    add_merged(conn, "p1", [{"type": "background", "target_section": "experiments"}])
    result = body(invoke(["revive", "--gaps"]))
    assert result["revived"] == 0
    assert conn.execute("SELECT status FROM papers WHERE paper_id='p1'").fetchone()[0] == "low_priority"


def test_revive_paper_id_discarded_rescreens(isolated, conn):
    add_paper(conn, "p1", status="discarded")
    conn.execute(
        "INSERT INTO screening_votes (paper_id, agent_id, uses_json, topic_relevant, created_at)"
        " VALUES ('p1', 'A', '[]', 0, ?)",
        (iso_now(),),
    )
    add_merged(conn, "p1", [])
    result = body(invoke(["revive", "--paper-id", "p1"]))
    assert result["revived"] == 1
    assert conn.execute("SELECT status FROM papers WHERE paper_id='p1'").fetchone()[0] == "candidate"
    assert conn.execute("SELECT COUNT(*) FROM screening_votes WHERE paper_id='p1'").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM screening_merged WHERE paper_id='p1'").fetchone()[0] == 0
    log = conn.execute("SELECT from_status, trigger_cell, round FROM revival_log").fetchone()
    assert (log["from_status"], log["trigger_cell"], log["round"]) == ("discarded", "manual", 1)


def test_revive_paper_id_low_priority_enqueues(isolated, conn):
    add_paper(conn, "p1", status="low_priority")
    add_merged(conn, "p1", [{"type": "baseline", "target_section": "experiments"}])
    result = body(invoke(["revive", "--paper-id", "p1"]))
    assert result["revived"] == 1
    assert conn.execute("SELECT status FROM papers WHERE paper_id='p1'").fetchone()[0] == "queued"
    assert conn.execute("SELECT priority FROM fetch_queue WHERE paper_id='p1'").fetchone()[0] == 0.5


def test_revive_respects_max_rounds(isolated, conn):
    add_paper(conn, "p1", status="low_priority")
    conn.execute("UPDATE papers SET revival_rounds = 2 WHERE paper_id='p1'")
    add_merged(conn, "p1", [{"type": "baseline", "target_section": "experiments"}])
    result = body(invoke(["revive", "--type", "baseline", "--section", "experiments"]))
    assert result["revived"] == 0
    assert conn.execute("SELECT status FROM papers WHERE paper_id='p1'").fetchone()[0] == "low_priority"


def test_revive_without_target_errors(isolated):
    result = invoke(["revive"])
    assert result.exit_code == 1
    assert "revive needs" in body(result)["reason"]
