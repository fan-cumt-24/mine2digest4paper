"""阶段 B 反证与审计：next-caveat / add-caveat / audit / submit-audit / reject-card。"""

import json

import pymupdf
from conftest import add_paper
from typer.testing import CliRunner

from mine2digest import store
from mine2digest.cli import app
from mine2digest.common import iso_now

runner = CliRunner()

PAGE1 = "our baseline method achieves ninety percent accuracy"


def invoke(args, input_text=None):
    return runner.invoke(app, args, input=input_text)


def body(result):
    return json.loads(result.stdout)


def make_paper(conn, pid="p1", title="Paper One", source="arxiv", status="confirmed"):
    slug = store.slugify(pid, title, None)
    pdir = store.paper_dir(slug)
    pdir.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), PAGE1)
    doc.save(str(pdir / "paper.pdf"))
    doc.close()
    store.write_meta(slug, {"paper_id": pid, "title": title})
    add_paper(conn, pid, title=title, status=status)
    conn.execute("UPDATE papers SET source = ? WHERE paper_id = ?", (source, pid))
    return slug


def add_card(
    conn,
    card_id,
    paper_id="p1",
    use_type="baseline",
    section="experiments",
    confidence=0.9,
    stage="fulltext_confirmed",
    caveat=None,
):
    conn.execute(
        "INSERT INTO evidence_cards"
        " (card_id, paper_id, use_type, target_section, claim, source_span, caveat,"
        "  confidence, stage, locator_status, created_at)"
        " VALUES (?, ?, ?, ?, 'claim', 'span', ?, ?, ?, 'verified', ?)",
        (card_id, paper_id, use_type, section, caveat, confidence, stage, iso_now()),
    )


def test_next_caveat_lists_confirmed_cards_without_caveat(isolated, conn):
    make_paper(conn, "p1")
    add_card(conn, "c1")
    add_card(conn, "c2", caveat="already noted")
    result = body(invoke(["next-caveat", "--limit", "5"]))
    assert result["count"] == 1
    assert result["cards"][0]["card_id"] == "c1"
    assert result["cards"][0]["paper_id"] == "p1"
    # 页文本按论文去重，放在 papers 段，不在每张卡上重复
    assert result["papers"][0]["paper_id"] == "p1"
    assert result["papers"][0]["pages"][0]["text"].strip()


def test_add_caveat_sets_text_and_confidence(isolated, conn):
    make_paper(conn, "p1")
    add_card(conn, "c1", confidence=0.9)
    result = body(
        invoke(["add-caveat", "--card-id", "c1"], input_text=json.dumps({"caveat": "n=1 only", "confidence": 0.4}))
    )
    assert result["ok"] is True
    row = conn.execute("SELECT caveat, confidence FROM evidence_cards WHERE card_id='c1'").fetchone()
    assert row["caveat"] == "n=1 only"
    assert row["confidence"] == 0.4


def test_add_caveat_keeps_confidence_when_omitted(isolated, conn):
    make_paper(conn, "p1")
    add_card(conn, "c1", confidence=0.8)
    body(invoke(["add-caveat", "--card-id", "c1"], input_text=json.dumps({"caveat": "weak"})))
    assert conn.execute("SELECT confidence FROM evidence_cards WHERE card_id='c1'").fetchone()[0] == 0.8


def test_add_caveat_requires_text_and_known_card(isolated, conn):
    make_paper(conn, "p1")
    add_card(conn, "c1")
    missing = invoke(["add-caveat", "--card-id", "c1"], input_text=json.dumps({"caveat": "   "}))
    assert missing.exit_code == 1
    unknown = invoke(["add-caveat", "--card-id", "nope"], input_text=json.dumps({"caveat": "x"}))
    assert unknown.exit_code == 1
    assert "unknown card_id" in body(unknown)["reason"]


def test_audit_samples_and_is_idempotent(isolated, conn):
    make_paper(conn, "p1")
    for i in range(10):
        add_card(conn, f"c{i}")
    first = body(invoke(["audit", "--rate", "0.5"]))
    assert first["sampled"] == 5
    second = body(invoke(["audit", "--rate", "1.0"]))
    assert second["sampled"] == 5  # 已抽样的不再重复
    assert conn.execute("SELECT COUNT(*) FROM audit_samples").fetchone()[0] == 10


def test_submit_audit_below_pass_min_lowers_source_weight(isolated, conn):
    make_paper(conn, "p1", source="arxiv")
    add_card(conn, "c1")
    add_card(conn, "c2")
    body(invoke(["audit", "--rate", "1.0"]))
    result = body(
        invoke(
            ["submit-audit"],
            input_text=json.dumps(
                {"results": [{"card_id": "c1", "passes": False, "reason": "hallucinated"},
                             {"card_id": "c2", "passes": False}]}
            ),
        )
    )
    assert result["updated"] == 2
    assert result["reliability"]["arxiv"]["pass_rate"] == 0.0
    weight = conn.execute("SELECT weight FROM source_reliability WHERE source='arxiv'").fetchone()[0]
    assert weight == 0.9
    # 允许裸 dict / 单对象
    single = body(invoke(["submit-audit"], input_text=json.dumps({"card_id": "c1", "passes": True})))
    assert single["updated"] == 1


def test_submit_audit_is_idempotent(isolated, conn):
    make_paper(conn, "p1", source="arxiv")
    add_card(conn, "c1")
    add_card(conn, "c2")
    body(invoke(["audit", "--rate", "1.0"]))
    failing = json.dumps(
        {"results": [{"card_id": "c1", "passes": False}, {"card_id": "c2", "passes": False}]}
    )
    body(invoke(["submit-audit"], input_text=failing))
    body(invoke(["submit-audit"], input_text=failing))
    weight = conn.execute("SELECT weight FROM source_reliability WHERE source='arxiv'").fetchone()[0]
    assert weight == 0.9  # 不累乘成 0.81


def test_submit_audit_passing_keeps_weight(isolated, conn):
    make_paper(conn, "p1", source="pmc")
    add_card(conn, "c1")
    body(invoke(["audit", "--rate", "1.0"]))
    body(invoke(["submit-audit"], input_text=json.dumps([{"card_id": "c1", "passes": True}])))
    weight = conn.execute("SELECT weight FROM source_reliability WHERE source='pmc'").fetchone()[0]
    assert weight == 1.0


def test_reject_card_records_reason(isolated, conn):
    make_paper(conn, "p1")
    add_card(conn, "c1")
    result = body(invoke(["reject-card", "--card-id", "c1", "--reason", "out of scope"]))
    assert result["stage"] == "rejected"
    row = conn.execute("SELECT stage, reject_reason FROM evidence_cards WHERE card_id='c1'").fetchone()
    assert row["stage"] == "rejected"
    assert row["reject_reason"] == "out of scope"

    unknown = invoke(["reject-card", "--card-id", "nope"])
    assert unknown.exit_code == 1
    assert "unknown card_id" in body(unknown)["reason"]
