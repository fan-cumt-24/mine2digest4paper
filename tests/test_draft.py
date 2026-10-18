"""阶段 C4：逐节草稿（骨架 + 打磨 prompt）与 evidence_ref 回溯校验。"""

import json
import sys
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


def ensure_paper(conn, pid, status="confirmed"):
    row = conn.execute("SELECT 1 FROM papers WHERE paper_id = ?", (pid,)).fetchone()
    if row is None:
        add_paper(conn, pid, status=status)
    else:
        conn.execute("UPDATE papers SET status = ? WHERE paper_id = ?", (status, pid))


def card(
    conn,
    card_id,
    pid,
    use_type="baseline",
    section="intro",
    claim="CLIP generalizes across domains",
    source_span="CLIP generalizes",
    caveat=None,
    stage="fulltext_confirmed",
    locator_status="verified",
    ratio=95.0,
):
    ensure_paper(conn, pid)
    conn.execute(
        "INSERT INTO evidence_cards"
        " (card_id, paper_id, use_type, target_section, claim, source_span, caveat,"
        "  confidence, stage, locator_status, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, 0.8, ?, ?, ?)",
        (card_id, pid, use_type, section, claim, source_span, caveat, stage, locator_status, iso_now()),
    )
    conn.execute(
        "INSERT INTO locator_checks (card_id, match_ratio, matched_text, checked_at)"
        " VALUES (?, ?, ?, ?)",
        (card_id, ratio, source_span, iso_now()),
    )


def write_template(section="intro", pid="p1", provisional=False, source_span="Structural excerpt."):
    Path("outputs").mkdir(exist_ok=True)
    Path("outputs/structure_templates.json").write_text(
        json.dumps(
            {
                "generated_at": iso_now(),
                "target_venue": "ACL",
                "templates": [
                    {
                        "target_section": section,
                        "source_paper_id": pid,
                        "card_id": None,
                        "claim": None,
                        "source_span": source_span,
                        "confidence": None,
                        "title": "sample title",
                        "venue": "ACL",
                        "provisional": provisional,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


# --- draft ---


def test_draft_writes_skeleton_and_prompt(isolated, conn):
    card(conn, "c1", "p1", use_type="baseline", claim="CLIP is a strong baseline")
    card(conn, "c2", "p1", use_type="gap_evidence", claim="Prior work lacks OOD tests",
         caveat="small scale")

    result = body(invoke(["draft", "--section", "intro"]))

    assert result["ok"] is True
    assert result["claims"] == 2
    skeleton = Path(result["skeleton"]).read_text(encoding="utf-8")
    assert "# Draft: intro" in skeleton
    assert "- CLIP is a strong baseline [[card:c1]]" in skeleton
    assert "- Prior work lacks OOD tests [[card:c2]]" in skeleton
    assert "  - caveat: small scale" in skeleton
    assert "## Gaps" in skeleton
    assert "### baseline" in skeleton and "### gap_evidence" in skeleton

    prompt = Path(result["prompt"]).read_text(encoding="utf-8")
    assert "[[card:c1]]" in prompt
    assert "不得删除、改写" in prompt
    assert "Evidence-bound skeleton" in prompt
    assert result["polished"] is None


def test_draft_lists_gaps_and_groups(isolated, conn):
    card(conn, "c1", "p1", use_type="background")
    result = body(invoke(["draft", "--section", "method"]))
    assert result["ok"] is True
    assert "background" in result["gaps"]
    assert "baseline" in result["gaps"]
    skeleton = Path(result["skeleton"]).read_text(encoding="utf-8")
    assert "- baseline" in skeleton
    # 本节无卡
    assert result["claims"] == 0


def test_draft_excludes_rejected_paper_cards(isolated, conn):
    card(conn, "c1", "p1", use_type="baseline")
    conn.execute("UPDATE papers SET status = 'rejected' WHERE paper_id = 'p1'")
    result = body(invoke(["draft", "--section", "intro"]))
    assert result["claims"] == 0
    assert "baseline" in result["gaps"]


def test_draft_uses_and_skips_template(isolated, conn):
    card(conn, "c1", "p1", use_type="baseline")
    write_template(section="intro", pid="p1", provisional=True)

    result = body(invoke(["draft", "--section", "intro"]))
    assert result["template"]["provisional"] is True
    skeleton = Path(result["skeleton"]).read_text(encoding="utf-8")
    assert "## Style Reference" in skeleton
    assert "Structural excerpt." in skeleton

    conn.execute("UPDATE papers SET status = 'rejected' WHERE paper_id = 'p1'")
    result2 = body(invoke(["draft", "--section", "intro"]))
    assert result2["template"] is None
    assert "## Style Reference" not in Path(result2["skeleton"]).read_text(encoding="utf-8")


def test_draft_invalid_section(isolated):
    result = invoke(["draft", "--section", "nope"])
    assert result.exit_code == 1
    assert "illegal section" in body(result)["reason"]


def test_draft_polish_stub(isolated, conn, monkeypatch, tmp_path):
    card(conn, "c1", "p1", use_type="baseline")
    stub = tmp_path / "polish.py"
    stub.write_text(
        "import sys\nsys.stdout.write('# Polished intro\\n\\n' + sys.stdin.read())\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MINE2DIGEST_DRAFT_CMD", f'"{sys.executable}" "{stub}"')

    result = body(invoke(["draft", "--section", "intro", "--polish"]))
    assert result["ok"] is True
    assert result["polished"]
    polished = Path(result["polished"]).read_text(encoding="utf-8")
    assert polished.startswith("# Polished intro")
    assert "[[card:c1]]" in polished


def test_draft_polish_requires_command(isolated, conn, monkeypatch):
    card(conn, "c1", "p1", use_type="baseline")
    monkeypatch.delenv("MINE2DIGEST_DRAFT_CMD", raising=False)
    result = invoke(["draft", "--section", "intro", "--polish"])
    assert result.exit_code == 1
    assert "not configured" in body(result)["reason"]


# --- verify ---


def write_draft(text):
    path = Path("drafts/custom.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_verify_valid_refs(isolated, conn):
    card(conn, "c1", "p1")
    path = write_draft("# Draft\n\n## Claims\n\n- claim [[card:c1]]\n")
    result = body(invoke(["verify", "--file", path]))
    assert result["ok"] is True
    assert result["refs"] == 1
    assert result["invalid"] == []


def test_verify_unknown_card(isolated):
    path = write_draft("# Draft\n\n## Claims\n\n- claim [[card:missing]]\n")
    result = invoke(["verify", "--file", path])
    assert result.exit_code == 1
    payload = body(result)
    assert payload["reason"] == "invalid evidence_ref"
    assert payload["invalid"][0]["reason"] == "unknown card"


def test_verify_rejected_card_and_paper(isolated, conn):
    card(conn, "c1", "p1", stage="rejected")
    path = write_draft("## Claims\n- claim [[card:c1]]\n")
    result = body(invoke(["verify", "--file", path]))
    assert result["ok"] is False
    assert result["invalid"][0]["reason"] == "card rejected"

    conn.execute("UPDATE evidence_cards SET stage = 'fulltext_confirmed' WHERE card_id = 'c1'")
    conn.execute("UPDATE papers SET status = 'rejected' WHERE paper_id = 'p1'")
    result2 = body(invoke(["verify", "--file", path]))
    assert result2["ok"] is False
    assert result2["invalid"][0]["reason"] == "paper rejected"


def test_verify_low_locator_ratio(isolated, conn):
    card(conn, "c1", "p1", ratio=50.0)
    path = write_draft("## Claims\n- claim [[card:c1]]\n")
    result = body(invoke(["verify", "--file", path]))
    assert result["ok"] is False
    assert "locator ratio" in result["invalid"][0]["reason"]


def test_verify_unreferenced_claims(isolated, conn):
    card(conn, "c1", "p1")
    path = write_draft(
        "## Claims\n"
        "- with ref [[card:c1]]\n"
        "- no ref here\n"
        "  - caveat: nested line is not a claim\n"
        "## Gaps\n"
        "- baseline\n"
    )
    result = body(invoke(["verify", "--file", path]))
    assert result["ok"] is True
    assert len(result["unreferenced"]) == 1

    strict = invoke(["verify", "--file", path, "--require-refs"])
    assert strict.exit_code == 1
    assert body(strict)["reason"] == "unreferenced claims"


def test_verify_recheck_uses_pages(isolated, conn, monkeypatch):
    card(conn, "c1", "p1", source_span="CLIP generalizes")
    monkeypatch.setattr(
        "mine2digest.commands.b_read._page_texts_for",
        lambda conn, pid: {1: "CLIP generalizes well across domains."},
    )
    path = write_draft("## Claims\n- claim [[card:c1]]\n")
    result = body(invoke(["verify", "--file", path, "--recheck"]))
    assert result["ok"] is True


def test_verify_missing_file(isolated):
    result = invoke(["verify", "--file", "nope.md"])
    assert result.exit_code == 1
    assert "not found" in body(result)["reason"]
