"""阶段 B 阅读：next-reading / add-card / verify-card（含 locator 硬门回滚）。"""

import json
from pathlib import Path

import pymupdf
from conftest import add_paper
from typer.testing import CliRunner

from mine2digest import store
from mine2digest.cli import app
from mine2digest.common import iso_now

runner = CliRunner()

PAGE1 = "our baseline method achieves ninety percent accuracy"
PAGE2 = "the ablation removes the encoder module entirely"


def invoke(args, input_text=None):
    return runner.invoke(app, args, input=input_text)


def body(result):
    return json.loads(result.stdout)


def make_fetched_paper(conn, pid="p1", title="Paper One", pages=None):
    pages = pages or [PAGE1, PAGE2]
    slug = store.slugify(pid, title, None)
    pdir = store.paper_dir(slug)
    pdir.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()
    for text in pages:
        page = doc.new_page()
        page.insert_text((72, 72), text)
    doc.save(str(pdir / "paper.pdf"))
    doc.close()
    store.write_meta(slug, {"paper_id": pid, "title": title})

    add_paper(conn, pid, title=title, status="fetched")
    conn.execute(
        "INSERT INTO screening_merged (paper_id, merged_uses, decision, priority, disagreement, merged_at)"
        " VALUES (?, ?, 'fetch', 1.0, '[]', ?)",
        (pid, json.dumps([{"type": "baseline", "target_section": "experiments"}]), iso_now()),
    )
    conn.execute(
        "INSERT INTO confirmed_uses (paper_id, use_type, target_section, confidence, needs_fulltext_confirm)"
        " VALUES (?, 'baseline', 'experiments', 0.9, 0)",
        (pid,),
    )
    return slug


def card(pid="p1", span=PAGE1, locator="p.1", use_type="baseline", section="experiments"):
    return {
        "paper_id": pid,
        "use_type": use_type,
        "target_section": section,
        "claim": "X is the closest baseline",
        "source_span": span,
        "evidence_locator": locator,
        "method_snippet": "y = f(x)",
        "confidence": 0.9,
    }


def test_next_reading_returns_uses_and_pages(isolated, conn):
    make_fetched_paper(conn, "p1")
    result = body(invoke(["next-reading", "--limit", "5"]))
    assert result["count"] == 1
    paper = result["papers"][0]
    assert paper["merged_uses"] == [{"type": "baseline", "target_section": "experiments"}]
    assert [p["page"] for p in paper["pages"]] == [1, 2]
    assert "baseline" in paper["pages"][0]["text"]


def test_add_card_verifies_and_marks_confirming(isolated, conn):
    make_fetched_paper(conn, "p1")
    result = body(invoke(["add-card"], input_text=json.dumps(card())))
    assert result["count"] == 1

    row = conn.execute("SELECT use_type, locator_status, stage FROM evidence_cards").fetchone()
    assert row["locator_status"] == "verified"
    assert row["stage"] == "fulltext_confirmed"
    check = conn.execute("SELECT match_ratio FROM locator_checks").fetchone()
    assert check["match_ratio"] >= 75
    assert conn.execute("SELECT status FROM papers WHERE paper_id='p1'").fetchone()["status"] == "confirming"


def test_add_card_locator_failure_rolls_back_whole_batch(isolated, conn):
    make_fetched_paper(conn, "p1")
    good = card()
    bad = card(span="totally unrelated sentence zzzz", locator="p.2")
    result = invoke(["add-card"], input_text=json.dumps({"cards": [good, bad]}))
    assert result.exit_code == 1
    assert "locator failed" in body(result)["reason"]
    # 整批回滚：一张卡都不应写入
    assert conn.execute("SELECT COUNT(*) c FROM evidence_cards").fetchone()["c"] == 0
    assert conn.execute("SELECT status FROM papers WHERE paper_id='p1'").fetchone()["status"] == "fetched"


def test_add_card_rejects_illegal_enum(isolated, conn):
    make_fetched_paper(conn, "p1")
    result = invoke(["add-card"], input_text=json.dumps(card(use_type="nonsense")))
    assert result.exit_code == 1
    assert "illegal use type" in body(result)["reason"]
    assert conn.execute("SELECT COUNT(*) c FROM evidence_cards").fetchone()["c"] == 0


def test_add_card_accepts_single_object_and_list(isolated, conn):
    make_fetched_paper(conn, "p1")
    single = body(invoke(["add-card"], input_text=json.dumps(card())))
    assert single["count"] == 1
    multi = body(invoke(["add-card"], input_text=json.dumps([card(use_type="method_borrow", section="method")])))
    assert multi["count"] == 1
    assert conn.execute("SELECT COUNT(*) c FROM evidence_cards").fetchone()["c"] == 2


def test_verify_card_reruns_locator(isolated, conn):
    make_fetched_paper(conn, "p1")
    card_id = body(invoke(["add-card"], input_text=json.dumps(card())))["card_ids"][0]
    result = body(invoke(["verify-card", "--card-id", card_id]))
    assert result["locator_status"] == "verified"
    assert result["match_ratio"] >= 75


def test_verify_card_unknown_id(isolated):
    result = invoke(["verify-card", "--card-id", "nope"])
    assert result.exit_code == 1
    assert "unknown card_id" in body(result)["reason"]
