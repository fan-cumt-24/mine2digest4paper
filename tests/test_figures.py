"""阶段 C3：figures generate 包装 PaperBanana 并规范化产物。"""

import json
import sys
from pathlib import Path

from typer.testing import CliRunner

from mine2digest.cli import app

runner = CliRunner()

OK_STUB = """
import json
import sys
from pathlib import Path

out = Path.cwd()
(out / "render.png").write_bytes(bytes([137, 80, 78, 71]))
(out / "render.src.py").write_text("# generated source\\n", encoding="utf-8")
(out / "render.manifest.json").write_text(
    json.dumps({"renderer": "stub", "argv": sys.argv[1:]}), encoding="utf-8"
)
"""

FAIL_STUB = """
import sys
sys.stderr.write("boom")
sys.exit(3)
"""


def invoke(args, input_text=None):
    return runner.invoke(app, args, input=input_text)


def body(result):
    return json.loads(result.stdout)


def make_stub(tmp_path, name, content):
    stub = tmp_path / name
    stub.write_text(content, encoding="utf-8")
    return stub


def test_generate_normalizes_artifacts(isolated, tmp_path, monkeypatch):
    stub = make_stub(tmp_path, "ok_paperbanana.py", OK_STUB)
    monkeypatch.setenv("MINE2DIGEST_PAPERBANANA", f'"{sys.executable}" "{stub}"')
    inp = tmp_path / "method.txt"
    inp.write_text("step 1 -> step 2", encoding="utf-8")

    result = invoke(["figures", "generate", "--input", str(inp), "--caption", "A pipeline", "--fig-id", "fig-1"])
    assert result.exit_code == 0
    payload = body(result)
    assert payload["fig_id"] == "fig-1"
    assert Path("outputs/figures/fig-1.png").read_bytes() == bytes([137, 80, 78, 71])
    assert Path("outputs/figures/fig-1.src.py").read_text(encoding="utf-8") == "# generated source\n"
    manifest = json.loads(Path("outputs/figures/fig-1.manifest.json").read_text(encoding="utf-8"))
    assert manifest["fig_id"] == "fig-1"
    assert manifest["caption"] == "A pipeline"
    assert manifest["paperbanana"]["renderer"] == "stub"
    assert "generate" in manifest["command"]
    assert "--input" in manifest["command"]


def test_generate_default_fig_id(isolated, tmp_path, monkeypatch):
    stub = make_stub(tmp_path, "ok2_paperbanana.py", OK_STUB)
    monkeypatch.setenv("MINE2DIGEST_PAPERBANANA", f'"{sys.executable}" "{stub}"')
    inp = tmp_path / "Method Diagram.txt"
    inp.write_text("x", encoding="utf-8")
    result = body(invoke(["figures", "generate", "--input", str(inp), "--caption", "cap"]))
    assert result["fig_id"] == "fig-method-diagram"


def test_missing_caption_errors(isolated, tmp_path, monkeypatch):
    inp = tmp_path / "m.txt"
    inp.write_text("x", encoding="utf-8")
    result = invoke(["figures", "generate", "--input", str(inp), "--caption", "   "])
    assert result.exit_code == 1
    assert "caption is required" in body(result)["reason"]


def test_missing_input_errors(isolated):
    result = invoke(["figures", "generate", "--input", "nope.txt", "--caption", "cap"])
    assert result.exit_code == 1
    assert "input file not found" in body(result)["reason"]


def test_paperbanana_failure_propagates(isolated, tmp_path, monkeypatch):
    stub = make_stub(tmp_path, "fail_paperbanana.py", FAIL_STUB)
    monkeypatch.setenv("MINE2DIGEST_PAPERBANANA", f'"{sys.executable}" "{stub}"')
    inp = tmp_path / "m.txt"
    inp.write_text("x", encoding="utf-8")
    result = invoke(["figures", "generate", "--input", str(inp), "--caption", "cap"])
    assert result.exit_code == 1
    assert "paperbanana failed" in body(result)["reason"]
