"""审计抽样与 source_reliability 更新（抽样 B4，权重内化为查询排序因素）。"""

from __future__ import annotations

import sqlite3

from . import common


def sample_cards(conn: sqlite3.Connection, rate: float, now: str) -> list[sqlite3.Row]:
    """从尚未抽样的 fulltext_confirmed 卡中随机抽 n = round(rate * total) 张。"""
    total = conn.execute(
        "SELECT COUNT(*) FROM evidence_cards WHERE stage = 'fulltext_confirmed'"
        " AND card_id NOT IN (SELECT card_id FROM audit_samples)"
    ).fetchone()[0]
    if total <= 0:
        return []
    n = max(1, round(rate * total))
    rows = conn.execute(
        """
        SELECT card_id FROM evidence_cards
        WHERE stage = 'fulltext_confirmed'
          AND card_id NOT IN (SELECT card_id FROM audit_samples)
        ORDER BY RANDOM() LIMIT ?
        """,
        (n,),
    ).fetchall()
    for row in rows:
        conn.execute(
            "INSERT OR IGNORE INTO audit_samples (card_id, passes, reason, sampled_at)"
            " VALUES (?, NULL, NULL, ?)",
            (row["card_id"], now),
        )
    return rows


def update_source_reliability(
    conn: sqlite3.Connection,
    window: int,
    pass_min: float,
    floor: float,
    now: str,
) -> dict[str, dict]:
    """按论文 source 聚合最近 window 条审计样本，幂等重算 weight（不累乘）。

    pass_rate >= pass_min → 1.0；否则 → max(floor, 0.9)。重复调用同一窗口得到相同结果。
    """
    rows = conn.execute(
        """
        SELECT p.source AS source,
               COUNT(*) AS n,
               SUM(s.passes) AS passed
        FROM (
            SELECT card_id, passes, sampled_at FROM audit_samples
            WHERE passes IS NOT NULL
            ORDER BY sampled_at DESC LIMIT ?
        ) s
        JOIN evidence_cards c ON c.card_id = s.card_id
        JOIN papers p ON p.paper_id = c.paper_id
        WHERE p.source IS NOT NULL
        GROUP BY p.source
        """,
        (window,),
    ).fetchall()

    summary: dict[str, dict] = {}
    for row in rows:
        source = row["source"]
        n = row["n"] or 0
        passed = row["passed"] or 0
        pass_rate = passed / n if n else 0.0
        weight = 1.0 if pass_rate >= pass_min else max(floor, 0.9)
        conn.execute(
            "INSERT OR REPLACE INTO source_reliability (source, weight, updated_at) VALUES (?, ?, ?)",
            (source, weight, now),
        )
        summary[source] = {"n": n, "pass_rate": round(pass_rate, 4), "weight": weight}
    return summary
