"""第 2 层：伪 agent 全链路（子进程）契约测试。

模拟一个真实 agent 的调用方式：只用 `next-candidates` 的 stdout、
只用 `submit-vote` 的 stdin，最后 `tally-votes`。全程不碰 DB。
"""

import json

from conftest import add_paper

from _agentutil import run_cli, run_json


def _fake_agent(agent_id):
    """按既定策略为一个 agent 跑完“拉取 -> 投票”，返回本轮拉到的候选 id。"""
    proc, body = run_json(["next-candidates", "--agent-id", agent_id, "--limit", "100"])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert body["ok"] is True
    seen = []
    votes = []
    for cand in body["candidates"]:
        pid = cand["paper_id"]
        seen.append(pid)
        # 契约：候选必须带这些字段，agent 才能判断
        assert {"paper_id", "title", "abstract", "citation_contexts"} <= set(cand)
        if pid == "p3":  # 无关论文
            votes.append({"paper_id": pid, "topic_relevant": False, "uses": []})
        else:
            votes.append(
                {
                    "paper_id": pid,
                    "topic_relevant": True,
                    "uses": [
                        {
                            "type": "baseline",
                            "target_section": "experiments",
                            "anchor": "直接基线",
                            "confidence": 0.9,
                        }
                    ],
                }
            )
    proc, body = run_json(
        ["submit-vote", "--agent-id", agent_id],
        input_text=json.dumps({"votes": votes}, ensure_ascii=False),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert body["accepted"] == len(votes)
    return seen


def test_fake_agents_full_roundtrip(isolated, conn):
    for pid in ("p1", "p2", "p3"):
        add_paper(conn, pid)

    assert set(_fake_agent("A")) == {"p1", "p2", "p3"}
    assert set(_fake_agent("B")) == {"p1", "p2", "p3"}

    proc, body = run_json(["tally-votes"])
    assert proc.returncode == 0
    assert body == {"ok": True, "processed": 3, "fetch": 2, "low_priority": 0, "discard": 1}

    statuses = {
        r["paper_id"]: r["status"]
        for r in conn.execute("SELECT paper_id, status FROM papers").fetchall()
    }
    assert statuses == {"p1": "queued", "p2": "queued", "p3": "discarded"}


def test_subprocess_reads_only_stdout(isolated, conn):
    add_paper(conn, "p1")
    # 错误也必须走 stdout 的 JSON（退出码 1），不靠 stderr 解析
    proc, body = run_json(
        ["submit-vote", "--agent-id", "A"],
        input_text=json.dumps(
            {"paper_id": "p1", "uses": [{"type": "x", "target_section": "intro", "confidence": 0.9}]}
        ),
    )
    assert proc.returncode == 1
    assert body["ok"] is False
    assert "illegal use type" in body["reason"]


def test_subprocess_rejects_extra_fields(isolated, conn):
    add_paper(conn, "p1")
    proc, body = run_json(
        ["submit-vote", "--agent-id", "A"],
        input_text=json.dumps({"paper_id": "p1", "uses": [], "sneaky": True}),
    )
    assert proc.returncode == 1
    assert body["ok"] is False


def test_agent_cannot_vote_twice_differently_without_overwrite(isolated, conn):
    add_paper(conn, "p1")
    first = {"paper_id": "p1", "uses": [{"type": "baseline", "target_section": "experiments", "confidence": 0.9}]}
    run_json(["submit-vote", "--agent-id", "A"], input_text=json.dumps(first))
    run_json(["submit-vote", "--agent-id", "A"], input_text=json.dumps(first))
    n = conn.execute("SELECT COUNT(*) c FROM screening_votes").fetchone()["c"]
    assert n == 1
