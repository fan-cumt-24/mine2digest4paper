"""阶段 A 筛选 CLI 端到端用例。"""

import json

import pytest
from typer.testing import CliRunner

from conftest import add_paper

from mine2digest.cli import app

runner = CliRunner()


def invoke(args, input_text=None):
    return runner.invoke(app, args, input=input_text)


def payload(result):
    return json.loads(result.stdout)


def vote_obj(pid, uses, relevant=True):
    return {"paper_id": pid, "topic_relevant": relevant, "uses": uses}


def use(kind, section, confidence):
    return {"type": kind, "target_section": section, "anchor": "x", "confidence": confidence}


def test_init_creates_db(isolated):
    result = invoke(["init"])
    assert result.exit_code == 0
    body = payload(result)
    assert body["ok"] is True
    from mine2digest import db

    assert body["schema_version"] == db.SCHEMA_VERSION
    assert (isolated / "test.sqlite").exists()


def test_next_candidates_filters_already_voted(isolated, conn):
    add_paper(conn, "p1")
    add_paper(conn, "p2")

    r = invoke(
        ["submit-vote", "--agent-id", "A"],
        input_text=json.dumps(vote_obj("p1", [use("baseline", "experiments", 0.9)])),
    )
    assert r.exit_code == 0

    r = invoke(["next-candidates", "--agent-id", "A", "--limit", "10"])
    ids = [c["paper_id"] for c in payload(r)["candidates"]]
    assert ids == ["p2"]

    r = invoke(["next-candidates", "--agent-id", "B", "--limit", "10"])
    ids = {c["paper_id"] for c in payload(r)["candidates"]}
    assert ids == {"p1", "p2"}


def test_submit_vote_rejects_unknown_agent(isolated, conn):
    add_paper(conn, "p1")
    r = invoke(
        ["submit-vote", "--agent-id", "Z"],
        input_text=json.dumps(vote_obj("p1", [])),
    )
    assert r.exit_code == 1
    assert payload(r)["ok"] is False


def test_submit_vote_rejects_illegal_enum(isolated, conn):
    add_paper(conn, "p1")
    r = invoke(
        ["submit-vote", "--agent-id", "A"],
        input_text=json.dumps(vote_obj("p1", [use("nonsense", "experiments", 0.9)])),
    )
    assert r.exit_code == 1
    assert "illegal use type" in payload(r)["reason"]


def test_submit_vote_rejects_unknown_paper(isolated):
    r = invoke(
        ["submit-vote", "--agent-id", "A"],
        input_text=json.dumps(vote_obj("ghost", [])),
    )
    assert r.exit_code == 1
    assert "unknown paper_id" in payload(r)["reason"]


def test_submit_vote_is_idempotent_per_agent(isolated, conn):
    add_paper(conn, "p1")
    body = json.dumps(vote_obj("p1", [use("baseline", "experiments", 0.9)]))
    invoke(["submit-vote", "--agent-id", "A"], input_text=body)
    invoke(["submit-vote", "--agent-id", "A"], input_text=body)
    rows = conn.execute("SELECT COUNT(*) c FROM screening_votes WHERE paper_id='p1' AND agent_id='A'").fetchone()
    assert rows["c"] == 1


def test_tally_fetch_end_to_end(isolated, conn):
    add_paper(conn, "p1")
    invoke(
        ["submit-vote", "--agent-id", "A"],
        input_text=json.dumps(vote_obj("p1", [use("baseline", "experiments", 0.9)])),
    )
    invoke(
        ["submit-vote", "--agent-id", "B"],
        input_text=json.dumps(vote_obj("p1", [use("baseline", "experiments", 0.85)])),
    )

    r = invoke(["tally-votes"])
    body = payload(r)
    assert body["ok"] is True
    assert body["fetch"] == 1

    paper = conn.execute("SELECT status FROM papers WHERE paper_id='p1'").fetchone()
    assert paper["status"] == "queued"

    queue = conn.execute("SELECT priority, claimed_at FROM fetch_queue WHERE paper_id='p1'").fetchone()
    assert queue["priority"] == 1.0
    assert queue["claimed_at"] is None

    confirmed = conn.execute("SELECT use_type, target_section FROM confirmed_uses WHERE paper_id='p1'").fetchall()
    assert [(c["use_type"], c["target_section"]) for c in confirmed] == [("baseline", "experiments")]


def test_tally_low_priority_and_discard(isolated, conn):
    add_paper(conn, "low")
    add_paper(conn, "drop")
    invoke(["submit-vote", "--agent-id", "A"],
           input_text=json.dumps(vote_obj("low", [use("background", "intro", 0.9)])))
    invoke(["submit-vote", "--agent-id", "B"],
           input_text=json.dumps(vote_obj("low", [use("background", "intro", 0.9)])))
    invoke(["submit-vote", "--agent-id", "A"],
           input_text=json.dumps(vote_obj("drop", [], relevant=False)))
    invoke(["submit-vote", "--agent-id", "B"],
           input_text=json.dumps(vote_obj("drop", [], relevant=False)))

    body = payload(invoke(["tally-votes"]))
    assert body["low_priority"] == 1
    assert body["discard"] == 1

    assert conn.execute("SELECT status FROM papers WHERE paper_id='low'").fetchone()["status"] == "low_priority"
    assert conn.execute("SELECT status FROM papers WHERE paper_id='drop'").fetchone()["status"] == "discarded"


def test_tally_only_processes_double_voted(isolated, conn):
    add_paper(conn, "p1")
    add_paper(conn, "p2")
    invoke(["submit-vote", "--agent-id", "A"],
           input_text=json.dumps(vote_obj("p1", [use("baseline", "experiments", 0.9)])))
    invoke(["submit-vote", "--agent-id", "B"],
           input_text=json.dumps(vote_obj("p2", [use("baseline", "experiments", 0.9)])))

    body = payload(invoke(["tally-votes"]))
    assert body["processed"] == 0


def test_tally_is_idempotent(isolated, conn):
    add_paper(conn, "p1")
    invoke(["submit-vote", "--agent-id", "A"],
           input_text=json.dumps(vote_obj("p1", [use("baseline", "experiments", 0.9)])))
    invoke(["submit-vote", "--agent-id", "B"],
           input_text=json.dumps(vote_obj("p1", [use("baseline", "experiments", 0.9)])))

    first = payload(invoke(["tally-votes"]))
    second = payload(invoke(["tally-votes"]))
    assert first["processed"] == 1
    assert second["processed"] == 0

    rows = conn.execute("SELECT COUNT(*) c FROM confirmed_uses WHERE paper_id='p1'").fetchone()
    assert rows["c"] == 1


def test_transition_recorded(isolated, conn):
    add_paper(conn, "p1")
    invoke(["submit-vote", "--agent-id", "A"],
           input_text=json.dumps(vote_obj("p1", [use("baseline", "experiments", 0.9)])))
    invoke(["submit-vote", "--agent-id", "B"],
           input_text=json.dumps(vote_obj("p1", [use("baseline", "experiments", 0.9)])))
    invoke(["tally-votes"])

    rows = conn.execute(
        "SELECT from_status, to_status, command FROM state_transitions WHERE paper_id='p1' ORDER BY id"
    ).fetchall()
    pairs = [(r["from_status"], r["to_status"]) for r in rows]
    assert ("candidate", "screened") in pairs
    assert ("screened", "queued") in pairs


def test_cli_writes_audit_log(isolated):
    invoke(["init"])
    log_file = isolated / "logs" / "cli.jsonl"
    assert log_file.exists()
    lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    assert any(json.loads(line)["command"] == "init" for line in lines)


def test_draft_requires_section(isolated):
    r = invoke(["draft"])
    assert r.exit_code != 0


@pytest.mark.parametrize("args", [["init"], ["tally-votes"]])
def test_commands_emit_valid_json(isolated, args):
    result = invoke(args)
    payload(result)  # 不抛异常即 JSON 合法
