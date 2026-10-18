"""阶段 A · 筛选：next-candidates / submit-vote / tally-votes。"""

from __future__ import annotations

import json
from typing import Any

from .. import common, db, merge
from ..models import Vote, VoteFile


def _require_agent(labels: dict, agent_id: str) -> None:
    allowed = labels.get("agent_ids") or []
    if agent_id not in allowed:
        raise ValueError(f"unknown agent_id {agent_id!r}; expected one of {allowed}")


def _validate_use_enums(labels: dict, vote: Vote) -> None:
    use_types = set(labels["use_types"])
    sections = set(labels["target_sections"])
    for use in vote.uses:
        if use.type not in use_types:
            raise ValueError(f"illegal use type: {use.type!r}")
        if use.target_section not in sections:
            raise ValueError(f"illegal target_section: {use.target_section!r}")


def _as_votes(payload: Any) -> list[Vote]:
    if isinstance(payload, dict) and "votes" in payload:
        return VoteFile(**payload).votes
    if isinstance(payload, dict):
        return [Vote(**payload)]
    if isinstance(payload, list):
        return [Vote(**item) for item in payload]
    raise ValueError("unrecognized vote payload (expected object, {votes:[...]}, or list)")


def next_candidates(agent_id: str, limit: int) -> dict:
    labels = common.load_labels()
    _require_agent(labels, agent_id)
    conn = db.connect()
    try:
        rows = conn.execute(
            """
            SELECT p.paper_id, p.doi, p.title, p.abstract, p.year, p.venue, p.source, p.status
            FROM papers p
            WHERE p.status IN ('candidate', 'screened')
              AND NOT EXISTS (
                SELECT 1 FROM screening_votes v
                WHERE v.paper_id = p.paper_id AND v.agent_id = ?
              )
            ORDER BY p.added_at, p.paper_id
            LIMIT ?
            """,
            (agent_id, limit),
        ).fetchall()
        candidates = []
        for row in rows:
            item = dict(row)
            contexts = conn.execute(
                "SELECT src_id, contexts FROM relations WHERE dst_id = ?", (row["paper_id"],)
            ).fetchall()
            item["citation_contexts"] = [
                {"src_id": c["src_id"], "contexts": json.loads(c["contexts"]) if c["contexts"] else []}
                for c in contexts
            ]
            candidates.append(item)
        return common.ok(agent_id=agent_id, count=len(candidates), candidates=candidates)
    finally:
        conn.close()


def submit_vote(agent_id: str, file: str | None) -> dict:
    labels = common.load_labels()
    _require_agent(labels, agent_id)
    votes = _as_votes(common.parse_json(common.read_input(file)))
    conn = db.connect()
    now = common.iso_now()
    accepted = 0
    try:
        with db.tx(conn):
            for vote in votes:
                _validate_use_enums(labels, vote)
                row = conn.execute(
                    "SELECT status FROM papers WHERE paper_id = ?", (vote.paper_id,)
                ).fetchone()
                if row is None:
                    raise ValueError(f"unknown paper_id: {vote.paper_id}")
                if row["status"] not in ("candidate", "screened"):
                    raise ValueError(
                        f"paper {vote.paper_id} not screenable (status={row['status']})"
                    )
                conn.execute(
                    """
                    INSERT INTO screening_votes (paper_id, agent_id, uses_json, topic_relevant, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(paper_id, agent_id) DO UPDATE SET
                      uses_json = excluded.uses_json,
                      topic_relevant = excluded.topic_relevant,
                      created_at = excluded.created_at
                    """,
                    (
                        vote.paper_id,
                        agent_id,
                        json.dumps([u.model_dump() for u in vote.uses], ensure_ascii=False),
                        1 if vote.topic_relevant else 0,
                        now,
                    ),
                )
                if row["status"] == "candidate":
                    db.transition(conn, vote.paper_id, "screened", "submit-vote")
                accepted += 1
        return common.ok(agent_id=agent_id, accepted=accepted)
    finally:
        conn.close()


def _load_vote(conn, paper_id: str, agent_id: str) -> dict:
    row = conn.execute(
        "SELECT uses_json, topic_relevant FROM screening_votes WHERE paper_id = ? AND agent_id = ?",
        (paper_id, agent_id),
    ).fetchone()
    return {
        "paper_id": paper_id,
        "topic_relevant": bool(row["topic_relevant"]),
        "uses": json.loads(row["uses_json"]),
    }


def tally_votes(limit: int | None = None) -> dict:
    labels = common.load_labels()
    agents = labels.get("agent_ids") or []
    if len(agents) < 2:
        raise ValueError("labels.agent_ids needs >= 2 entries")
    agent_a, agent_b = agents[0], agents[1]
    conn = db.connect()
    now = common.iso_now()
    counts = {"processed": 0, "fetch": 0, "low_priority": 0, "discard": 0}
    try:
        with db.tx(conn):
            sql = """
                SELECT p.paper_id
                FROM papers p
                WHERE p.status IN ('candidate', 'screened')
                  AND (
                    SELECT COUNT(DISTINCT v.agent_id)
                    FROM screening_votes v
                    WHERE v.paper_id = p.paper_id AND v.agent_id IN (?, ?)
                  ) = 2
                ORDER BY p.paper_id
            """
            params: list[Any] = [agent_a, agent_b]
            if limit:
                sql += " LIMIT ?"
                params.append(limit)
            paper_ids = [r["paper_id"] for r in conn.execute(sql, params).fetchall()]

            for paper_id in paper_ids:
                vote_a = _load_vote(conn, paper_id, agent_a)
                vote_b = _load_vote(conn, paper_id, agent_b)
                result = merge.merge_votes(vote_a, vote_b, labels)

                conn.execute(
                    """
                    INSERT OR REPLACE INTO screening_merged
                      (paper_id, merged_uses, decision, priority, disagreement, merged_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        paper_id,
                        json.dumps(result["confirmed_uses"], ensure_ascii=False),
                        result["decision"],
                        result["priority"],
                        json.dumps(result["disagreement"], ensure_ascii=False),
                        now,
                    ),
                )
                conn.execute("DELETE FROM confirmed_uses WHERE paper_id = ?", (paper_id,))
                for use in result["confirmed_uses"]:
                    conn.execute(
                        """
                        INSERT INTO confirmed_uses
                          (paper_id, use_type, target_section, confidence, needs_fulltext_confirm)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            paper_id,
                            use["type"],
                            use["target_section"],
                            use["confidence"],
                            1 if use["needs_fulltext_confirm"] else 0,
                        ),
                    )

                if result["decision"] == "fetch":
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO fetch_queue
                          (paper_id, priority, enqueued_at, claimed_at, claimed_by)
                        VALUES (?, ?, ?, NULL, NULL)
                        """,
                        (paper_id, result["priority"], now),
                    )
                    db.transition(conn, paper_id, "queued", "tally-votes")
                elif result["decision"] == "low_priority":
                    db.transition(conn, paper_id, "low_priority", "tally-votes")
                else:
                    db.transition(conn, paper_id, "discarded", "tally-votes")

                counts["processed"] += 1
                counts[result["decision"]] += 1

        return common.ok(**counts)
    finally:
        conn.close()
