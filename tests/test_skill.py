"""SKILL.md 与 CLI/资产保持一致，且不泄露密钥。"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
CANONICAL = REPO / ".opencode" / "skills" / "mine2digest" / "SKILL.md"
PORTABLE = REPO / "SKILL.md"
TOOLS_JSON = REPO / "docs" / "tools.json"

PROMPT_FILES = [
    REPO / "config" / "screening_prompts" / "agent_a.md",
    REPO / "config" / "screening_prompts" / "agent_b.md",
    REPO / "config" / "reading_prompts" / "reader.md",
    REPO / "config" / "reading_prompts" / "caveat.md",
    REPO / "config" / "reading_prompts" / "auditor.md",
]

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def _frontmatter(text: str) -> dict:
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
    assert match, "missing YAML frontmatter"
    return yaml.safe_load(match.group(1))


def test_frontmatter():
    text = CANONICAL.read_text(encoding="utf-8")
    meta = _frontmatter(text)
    assert meta["name"] == "mine2digest"
    assert meta["description"].strip()
    lowered = meta["description"].lower()
    assert "mine2digest" in lowered
    assert "screen" in lowered or "literature" in lowered


def test_covers_every_command():
    body = CANONICAL.read_text(encoding="utf-8")
    tools = json.loads(TOOLS_JSON.read_text(encoding="utf-8"))["tools"]
    missing = [tool["name"] for tool in tools if tool["name"] not in body]
    assert not missing, f"SKILL.md missing commands: {missing}"


def test_multi_agent_protocol_present():
    body = CANONICAL.read_text(encoding="utf-8")
    for marker in ("Screener A", "Screener B", "Reader", "Caveat", "Auditor", "独立性", "双盲"):
        assert marker in body, marker
    assert "task(" in body and "subagent_type" in body
    assert "agent-id A" in body and "agent-id B" in body


def test_prompt_assets_exist():
    for path in PROMPT_FILES:
        assert path.exists(), path


def test_references_tools_schema_and_env():
    body = CANONICAL.read_text(encoding="utf-8")
    assert "docs/tools.json" in body
    assert "scripts/export_tools.py" in body
    assert ".opencode/skills/mine2digest/.env" in body
    assert "UNPAYWALL_EMAIL" in body


def test_no_secrets_leaked():
    for path in (CANONICAL, PORTABLE):
        text = path.read_text(encoding="utf-8")
        assert not EMAIL_RE.search(text), f"email-like secret in {path}"


def test_portable_entry_points_to_canonical():
    text = PORTABLE.read_text(encoding="utf-8")
    meta = _frontmatter(text)
    assert meta["name"] == "mine2digest"
    assert meta["description"].strip()
    assert ".opencode/skills/mine2digest/SKILL.md" in text
    assert "docs/tools.json" in text
