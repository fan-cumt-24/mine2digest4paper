"""第 5 层：真实 agent 冒烟测试（默认跳过）。

需要外部提供“agent 命令”：它从 stdin 读 prompt、向 stdout 写一个投票 JSON。
设置 MINE2DIGEST_AGENT_CMD 后才会运行，例如：

    $env:MINE2DIGEST_AGENT_CMD = "python my_llm_agent.py"
    pytest -q -m integration

被测的关键点：真实 LLM 的输出能否通过 CLI 的 schema 校验（额外字段/枚举非法
会被直接拒绝）。
"""

import json
import os

import pytest

from _agentutil import run_json
from conftest import add_paper
from mine2digest.models import Vote

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("MINE2DIGEST_AGENT_CMD"),
        reason="set MINE2DIGEST_AGENT_CMD to run live agent smoke test",
    ),
]


def _call_agent(prompt: str) -> str:
    import subprocess

    cmd = os.environ["MINE2DIGEST_AGENT_CMD"]
    proc = subprocess.run(
        cmd, shell=True, input=prompt, capture_output=True, text=True, encoding="utf-8"
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def test_live_agent_output_passes_schema(isolated, conn):
    add_paper(conn, "p1", title="A Baseline for X", abstract="We propose ...")

    _, body = run_json(["next-candidates", "--agent-id", "A", "--limit", "1"])
    candidate = body["candidates"][0]

    prompt = (
        "Config: config/screening_prompts/agent_a.md\n"
        "Return ONE vote JSON for this candidate:\n"
        + json.dumps(candidate, ensure_ascii=False)
    )
    raw = _call_agent(prompt)
    vote = Vote.model_validate_json(raw)
    assert vote.paper_id == "p1"

    _, submit = run_json(
        ["submit-vote", "--agent-id", "A"],
        input_text=vote.model_dump_json(),
    )
    assert submit["ok"] is True
