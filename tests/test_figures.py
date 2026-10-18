"""阶段 C3：图表设计建议 —— extract / suggest / add-notes / verify。"""

import json
import sys
from pathlib import Path

import pymupdf
from conftest import add_paper
from typer.testing import CliRunner

from mine2digest import store
from mine2digest.cli import app

runner = CliRunner()

CAPTION = "Figure 1: Overview of the proposed two-stage pipeline."
CONTEXT = "As shown in Figure 1, the model first encodes then decodes."
TABLE_CAPTION = "Table 1: Ablation results on the benchmark."

POLISH_STUB = """
import sys
sys.stdin.read()
sys.stdout.write("# Polished design\\n\\n- (a) pipeline [fig:p1#Figure 1]\\n")
"""


def invoke(args, input_text=None):
    return runner.invoke(app, args, input=input_text)


def body(result):
    return json.loads(result.stdout)


def make_paper(conn, pid="p1", title="Paper One", pages=None, status="fetched"):
    pages = pages or [CAPTION, CONTEXT]
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
    add_paper(conn, pid, title=title, status=status)
    return slug


def extract_corpus():
    return body(invoke(["figures", "extract"]))


def test_extract_captures_figure_and_context(isolated, conn):
    make_paper(conn, "p1")
    result = extract_corpus()
    assert result["ok"] is True
    assert result["count"] == 1
    assert result["figure_count"] == 1
    doc = json.loads(Path("outputs/figure_corpus.json").read_text(encoding="utf-8"))
    entry = doc["entries"][0]
    assert entry["ref"] == "p1#Figure 1"
    assert entry["kind"] == "figure"
    assert "Overview" in entry["caption"]
    assert "encode" in entry["context"]
    assert entry["page"] == 1


def test_extract_kind_filter_and_render(isolated, conn):
    make_paper(conn, "p1", pages=[CAPTION, CONTEXT, TABLE_CAPTION])
    result = body(invoke(["figures", "extract", "--kind", "table", "--render"]))
    assert result["table_count"] == 1
    assert result["figure_count"] == 0
    assert result["rendered"] >= 1
    doc = json.loads(Path("outputs/figure_corpus.json").read_text(encoding="utf-8"))
    assert doc["entries"][0]["page_image"].endswith(".png")


def test_extract_skips_rejected_papers(isolated, conn):
    make_paper(conn, "p1", status="rejected")
    result = extract_corpus()
    assert result["count"] == 0


def test_extract_dedups_repeated_caption(isolated, conn):
    make_paper(conn, "p1", pages=[CAPTION, "Figure 1: Overview (continued).", CONTEXT])
    result = extract_corpus()
    assert result["count"] == 1
    doc = json.loads(Path("outputs/figure_corpus.json").read_text(encoding="utf-8"))
    assert len(doc["entries"]) == 1


def test_suggest_writes_skeleton_and_prompt(isolated, conn):
    make_paper(conn, "p1")
    intent = Path("intent.txt")
    intent.write_text("pipeline overview with encoder and decoder stages", encoding="utf-8")
    result = body(invoke(["figures", "suggest", "--input", str(intent), "--fig-id", "fig-1"]))
    assert result["ok"] is True
    assert result["references"] == 1
    assert "p1#Figure 1" in result["refs"]
    skeleton = Path("outputs/figures/fig-1.suggestion.md").read_text(encoding="utf-8")
    prompt = Path("outputs/figures/fig-1.prompt.md").read_text(encoding="utf-8")
    assert "[fig:p1#Figure 1]" in skeleton
    assert "Reference designs" in prompt


def test_suggest_default_fig_id_and_missing_input(isolated, conn):
    make_paper(conn, "p1")
    intent = Path("My Figure Idea.txt")
    intent.write_text("x", encoding="utf-8")
    result = body(invoke(["figures", "suggest", "--input", str(intent)]))
    assert result["fig_id"] == "fig-my-figure-idea"
    bad = invoke(["figures", "suggest", "--input", "nope.txt"])
    assert bad.exit_code == 1
    assert "input file not found" in body(bad)["reason"]


def test_suggest_polish_with_stub(isolated, conn, tmp_path, monkeypatch):
    make_paper(conn, "p1")
    stub = tmp_path / "polish.py"
    stub.write_text(POLISH_STUB, encoding="utf-8")
    monkeypatch.setenv("MINE2DIGEST_FIGURE_CMD", f'"{sys.executable}" "{stub}"')
    intent = Path("intent.txt")
    intent.write_text("pipeline", encoding="utf-8")
    result = body(invoke(["figures", "suggest", "--input", str(intent), "--fig-id", "fig-1", "--polish"]))
    assert result["ok"] is True
    assert "Polished design" in Path("outputs/figures/fig-1.polished.md").read_text(encoding="utf-8")


def test_suggest_polish_without_command_errors(isolated, conn, monkeypatch):
    make_paper(conn, "p1")
    monkeypatch.delenv("MINE2DIGEST_FIGURE_CMD", raising=False)
    intent = Path("intent.txt")
    intent.write_text("pipeline", encoding="utf-8")
    result = invoke(["figures", "suggest", "--input", str(intent), "--fig-id", "fig-1", "--polish"])
    assert result.exit_code == 1
    assert "polish command not configured" in body(result)["reason"]


def test_add_notes_and_verify(isolated, conn):
    make_paper(conn, "p1")
    extract_corpus()
    note = {"ref": "p1#Figure 1", "layout_type": "overview", "panels": ["(a) encoder"], "confidence": 0.8}
    result = body(invoke(["figures", "add-notes"], input_text=json.dumps(note)))
    assert result["ok"] is True
    assert result["total"] == 1

    suggestion = Path("outputs/figures/fig-1.suggestion.md")
    suggestion.write_text("# s\n\nsee [fig:p1#Figure 1]\n", encoding="utf-8")
    ok = body(invoke(["figures", "verify", "--file", str(suggestion)]))
    assert ok["ok"] is True and ok["invalid"] == []

    suggestion.write_text("# s\n\nsee [fig:ghost#Figure 9]\n", encoding="utf-8")
    bad = invoke(["figures", "verify", "--file", str(suggestion)])
    assert bad.exit_code == 1
    assert "ghost#Figure 9" in body(bad)["invalid"]


def test_add_notes_unknown_ref_errors(isolated, conn):
    make_paper(conn, "p1")
    extract_corpus()
    result = invoke(["figures", "add-notes"], input_text=json.dumps({"ref": "p1#Figure 99"}))
    assert result.exit_code == 1
    assert "unknown figure ref" in body(result)["reason"]


def test_add_notes_requires_corpus(isolated):
    result = invoke(["figures", "add-notes"], input_text=json.dumps({"ref": "p1#Figure 1"}))
    assert result.exit_code == 1
    assert "figure corpus not found" in body(result)["reason"]
