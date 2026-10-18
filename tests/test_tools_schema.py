"""docs/tools.json 必须与 CLI 同步（防止命令/参数漂移）。"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import export_tools  # noqa: E402

TOOLS_JSON = REPO / "docs" / "tools.json"

EXPECTED = {
    "init",
    "add-candidates",
    "add-relations",
    "next-candidates",
    "submit-vote",
    "tally-votes",
    "next-fetch",
    "report-fetch",
    "ingest-manual",
    "next-reading",
    "add-card",
    "verify-card",
    "next-caveat",
    "add-caveat",
    "audit",
    "submit-audit",
    "reject-card",
    "coverage",
    "query",
    "lookup",
    "export",
    "revive",
    "templates extract",
    "figures extract",
    "figures suggest",
    "figures add-notes",
    "figures verify",
    "draft",
    "verify",
    "clean",
}

STDIN_MODELS = {
    "submit-vote": "VoteFile",
    "add-card": "CardFile",
    "add-caveat": "Caveat",
    "submit-audit": "AuditFile",
    "figures add-notes": "FigureNoteFile",
}


def _document() -> dict:
    return export_tools.build_tools_document()


def test_committed_json_is_current():
    assert TOOLS_JSON.exists(), "docs/tools.json missing; run: python scripts/export_tools.py"
    current = TOOLS_JSON.read_text(encoding="utf-8")
    assert current == export_tools.render(_document()), (
        "docs/tools.json is stale; run: python scripts/export_tools.py"
    )


def test_all_commands_present():
    names = {tool["name"] for tool in _document()["tools"]}
    assert names == EXPECTED


def test_every_tool_is_well_formed():
    for tool in _document()["tools"]:
        assert tool["description"].strip(), tool["name"]
        assert tool["executor"]["type"] == "cli"
        assert tool["executor"]["command"][0] == "mine2digest"
        params = tool["parameters"]
        assert params["type"] == "object"
        assert params["additionalProperties"] is False
        for name in params["required"]:
            assert name in params["properties"], (tool["name"], name)


def test_no_commands_stubbed():
    stalled = [tool["name"] for tool in _document()["tools"] if tool["status"] != "ready"]
    assert not stalled, f"commands still stubbed: {stalled}"


def test_stdin_models_match_pydantic():
    by_name = {tool["name"]: tool for tool in _document()["tools"]}
    for command, model in STDIN_MODELS.items():
        stdin = by_name[command]["stdin"]
        assert stdin["model"] == model
        assert stdin["schema"]["type"] == "object"
        assert "properties" in stdin["schema"]


def test_jsonl_stdin_contracts():
    by_name = {tool["name"]: tool for tool in _document()["tools"]}
    for command in ("add-candidates", "add-relations"):
        stdin = by_name[command]["stdin"]
        assert stdin["format"] == "jsonl"
        assert stdin["description"].strip()


def test_unknown_param_has_type():
    for tool in _document()["tools"]:
        for name, prop in tool["parameters"]["properties"].items():
            assert prop["type"] in {"string", "integer", "number", "boolean", "array"}, (tool["name"], name)
