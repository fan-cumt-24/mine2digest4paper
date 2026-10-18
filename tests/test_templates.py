"""阶段 C1：结构模板抽取（style_sample 优先 + venue 回退 provisional）。"""

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


def paper(conn, pid, venue=None, status="confirmed"):
    add_paper(conn, pid, status=status)
    if venue is not None:
        conn.execute("UPDATE papers SET venue = ? WHERE paper_id = ?", (venue, pid))


def style_card(conn, card_id, pid, section="intro", stage="fulltext_confirmed", confidence=0.8):
    conn.execute(
        "INSERT INTO evidence_cards"
        " (card_id, paper_id, use_type, target_section, claim, source_span, confidence,"
        "  stage, locator_status, created_at)"
        " VALUES (?, ?, 'style_sample', ?, 'style claim', 'structural excerpt', ?, ?, 'verified', ?)",
        (card_id, pid, section, confidence, stage, iso_now()),
    )


def test_confirmed_style_sample_preferred(isolated, conn):
    paper(conn, "p1", venue="ACL")
    style_card(conn, "c1", "p1", section="intro", confidence=0.9)
    result = body(invoke(["templates", "extract", "--section", "intro"]))
    assert result["count"] == 1
    tpl = result["templates"][0]
    assert tpl["target_section"] == "intro"
    assert tpl["provisional"] is False
    assert tpl["card_id"] == "c1"
    assert tpl["source_paper_id"] == "p1"
    assert tpl["venue"] == "ACL"
    assert tpl["source_span"] == "structural excerpt"
    assert Path("outputs/structure_templates.json").exists()


def test_confirmed_picks_highest_confidence(isolated, conn):
    paper(conn, "p1", venue="ACL")
    paper(conn, "p2", venue="ACL")
    style_card(conn, "c-lo", "p1", section="intro", confidence=0.4)
    style_card(conn, "c-hi", "p2", section="intro", confidence=0.95)
    result = body(invoke(["templates", "extract", "--section", "intro"]))
    assert result["templates"][0]["card_id"] == "c-hi"


def test_venue_fallback_marks_provisional(isolated, conn):
    paper(conn, "p1", venue="ACL")
    style_card(conn, "c1", "p1", section="intro")
    paper(conn, "p2", venue="ACL", status="fetched")
    result = body(invoke(["templates", "extract"]))
    assert result["target_venue"] == "ACL"
    by_section = {t["target_section"]: t for t in result["templates"]}
    assert by_section["intro"]["provisional"] is False
    assert by_section["related_work"]["provisional"] is True
    assert by_section["related_work"]["source_paper_id"] == "p2"
    assert by_section["related_work"]["card_id"] is None


def test_rejected_source_card_skipped(isolated, conn):
    paper(conn, "p1", venue="ACL", status="rejected")
    style_card(conn, "c1", "p1", section="intro")
    result = body(invoke(["templates", "extract", "--section", "intro"]))
    assert result["count"] == 0
    assert result["target_venue"] is None


def test_no_confirmed_and_no_venue_yields_nothing(isolated, conn):
    paper(conn, "p1", venue=None, status="fetched")
    result = body(invoke(["templates", "extract"]))
    assert result["count"] == 0


def test_output_file_shape(isolated, conn):
    paper(conn, "p1", venue="NeurIPS")
    style_card(conn, "c1", "p1", section="method")
    body(invoke(["templates", "extract", "--section", "method"]))
    doc = json.loads(Path("outputs/structure_templates.json").read_text(encoding="utf-8"))
    assert set(doc) == {"generated_at", "target_venue", "templates"}
    assert doc["target_venue"] == "NeurIPS"
    assert doc["templates"][0]["target_section"] == "method"
