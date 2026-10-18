"""SQLite 连接、事务、初始化与状态机迁移守卫。"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

from . import common

SCHEMA_VERSION = 3

# 合法状态迁移表：唯一的状态机定义。
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "candidate": {"screened", "queued", "low_priority", "discarded", "fetched"},
    "screened": {"queued", "low_priority", "discarded", "fetched"},
    "queued": {"fetching", "low_priority", "fetched"},
    "fetching": {"fetched", "fetch_failed"},
    "fetch_failed": {"queued", "fetched"},
    "fetched": {"confirming"},
    "confirming": {"confirmed", "rejected"},
    "low_priority": {"queued", "candidate", "fetched"},
    "discarded": {"candidate"},
    "confirmed": set(),
    "rejected": set(),
}
# 说明：*->fetched 用于 ingest-manual（人工投放闭源 PDF），
# 可直接把 fetch_failed / low_priority / queued 的论文标记为已获取。

def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return any(row["name"] == column for row in conn.execute(f"PRAGMA table_info({table})"))


def _add_column(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    if not _column_exists(conn, table, column):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def _migration_2(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS paper_ids ("
        " paper_id TEXT NOT NULL, id_type TEXT NOT NULL, id_value TEXT NOT NULL,"
        " PRIMARY KEY (id_type, id_value))"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_paper_ids_paper ON paper_ids(paper_id)")


def _migration_3(conn: sqlite3.Connection) -> None:
    _add_column(conn, "evidence_cards", "reject_reason", "TEXT")


# 结构变更登记：{目标版本: [可幂等执行的函数, ...]}。
MIGRATIONS: dict[int, list[Callable[[sqlite3.Connection], None]]] = {
    2: [_migration_2],
    3: [_migration_3],
}


def db_path() -> str:
    env = os.environ.get("MINE2DIGEST_DB")
    if env:
        return env
    return str(common.wpath("db", "mine2digest.sqlite"))


def connect(path: str | None = None) -> sqlite3.Connection:
    p = path or db_path()
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p, isolation_level=None, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def tx(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """写事务：BEGIN IMMEDIATE，异常时回滚。"""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def read(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    return conn.execute(sql, params).fetchall()


def init_db(conn: sqlite3.Connection) -> None:
    """建库（幂等）并按需迁移到 SCHEMA_VERSION。"""
    schema = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
    conn.executescript(schema)
    _migrate(conn)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def _migrate(conn: sqlite3.Connection) -> None:
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    for version in range(current + 1, SCHEMA_VERSION + 1):
        for migrate in MIGRATIONS.get(version, []):
            migrate(conn)


def transition(
    conn: sqlite3.Connection,
    paper_id: str,
    to_status: str,
    command: str,
) -> str:
    """经状态机守卫迁移论文状态，并记录 state_transitions。返回最终状态。"""
    row = conn.execute("SELECT status FROM papers WHERE paper_id = ?", (paper_id,)).fetchone()
    if row is None:
        raise ValueError(f"unknown paper_id: {paper_id}")
    from_status = row["status"]
    if from_status == to_status:
        return from_status
    if to_status not in ALLOWED_TRANSITIONS.get(from_status, set()):
        raise ValueError(f"illegal transition: {from_status} -> {to_status}")
    now = common.iso_now()
    conn.execute(
        "UPDATE papers SET status = ?, updated_at = ? WHERE paper_id = ?",
        (to_status, now, paper_id),
    )
    conn.execute(
        "INSERT INTO state_transitions (paper_id, from_status, to_status, command, at)"
        " VALUES (?, ?, ?, ?, ?)",
        (paper_id, from_status, to_status, command, now),
    )
    return to_status
