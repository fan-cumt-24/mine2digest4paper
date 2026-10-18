"""第 3/4 层：Prompt 示例与模型的 schema 一致性 + 黄金样本回归。

目的：当 prompt 或模型字段改动时，这里能第一时间报警，避免 agent 按旧
schema 输出、CLI 静默误判。
"""

import json
import re
from pathlib import Path

import pytest

from mine2digest.models import Use, Vote, VoteFile

REPO = Path(__file__).resolve().parents[1]
PROMPTS = REPO / "config" / "screening_prompts"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

_JSON_BLOCK = re.compile(r"```json\s*\n(.*?)```", re.DOTALL)


def _first_json_block(md_path: Path) -> dict:
    text = md_path.read_text(encoding="utf-8")
    match = _JSON_BLOCK.search(text)
    assert match, f"no ```json block found in {md_path.name}"
    return json.loads(match.group(1))


@pytest.mark.parametrize("prompt", ["agent_a.md", "agent_b.md"])
def test_prompt_field_names_match_model(prompt):
    """prompt 里的示例必须与 Vote 的字段名一致（防止字段漂移）。"""
    example = _first_json_block(PROMPTS / prompt)
    assert {"paper_id", "topic_relevant", "uses"} <= set(example)
    assert set(example) == set(Vote.model_fields), "prompt 示例字段与 Vote 模型不一致"
    for use in example["uses"]:
        assert set(use) == set(Use.model_fields)


@pytest.mark.parametrize("prompt", ["agent_a.md", "agent_b.md"])
def test_prompt_example_validates(prompt):
    example = _first_json_block(PROMPTS / prompt)
    Vote.model_validate(example)  # extra="forbid" 下，字段漂移会直接失败


def test_vote_json_schema_is_stable():
    """可导出给 agent harness 的 JSON Schema。"""
    schema = Vote.model_json_schema()
    assert schema["type"] == "object"
    assert set(schema["required"]) == {"paper_id"}  # uses 有默认值，非必填
    assert schema["additionalProperties"] is False
    # 可序列化（harness 需要落盘/传输）
    assert json.loads(json.dumps(schema))["title"] == "Vote"


def test_unknown_field_is_rejected():
    with pytest.raises(Exception):
        Vote.model_validate({"paper_id": "p1", "uses": [], "oops": 1})


def _load_vote(path: Path):
    return VoteFile.model_validate_json(path.read_text(encoding="utf-8"))


def test_golden_fixtures_replay_to_expected_decision(isolated, conn):
    """录制的 agent A/B 输出重放：确认用途 fetch，单方标注进 disagreement。"""
    from conftest import add_paper

    add_paper(conn, "p1")
    for agent, path in (("A", FIXTURES / "agent_a.json"), ("B", FIXTURES / "agent_b.json")):
        vote_file = _load_vote(path)
        for vote in vote_file.votes:
            conn.execute(
                "INSERT INTO screening_votes (paper_id, agent_id, uses_json, topic_relevant, created_at)"
                " VALUES (?, ?, ?, ?, '2026-01-01T00:00:00+00:00')",
                (vote.paper_id, agent,
                 json.dumps([u.model_dump() for u in vote.uses], ensure_ascii=False),
                 1 if vote.topic_relevant else 0),
            )

    from mine2digest.commands.a_screen import tally_votes

    result = tally_votes()
    assert result["fetch"] == 1

    row = conn.execute("SELECT decision, priority, disagreement FROM screening_merged WHERE paper_id='p1'").fetchone()
    assert row["decision"] == "fetch"
    assert row["priority"] == 1.0
    disagreement = json.loads(row["disagreement"])
    assert [d["type"] for d in disagreement] == ["support_claim"]
    assert disagreement[0]["agents"] == ["A"]
