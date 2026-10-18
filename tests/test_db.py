"""db 层：状态机守卫与 schema 迁移。"""

import sqlite3

import pytest
from conftest import add_paper

from mine2digest import db


def _columns(conn, table):
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def test_transition_guard_rejects_illegal(isolated, conn):
    add_paper(conn, "p1")
    with pytest.raises(ValueError):
        db.transition(conn, "p1", "confirming", "test")  # candidate -> confirming 非法


def test_transition_noop_when_same_status(isolated, conn):
    add_paper(conn, "p1")
    assert db.transition(conn, "p1", "candidate", "test") == "candidate"
    n = conn.execute("SELECT COUNT(*) c FROM state_transitions").fetchone()["c"]
    assert n == 0


def test_transition_records_history(isolated, conn):
    add_paper(conn, "p1")
    db.transition(conn, "p1", "screened", "test")
    row = conn.execute("SELECT from_status, to_status, command FROM state_transitions").fetchone()
    assert (row["from_status"], row["to_status"], row["command"]) == ("candidate", "screened", "test")


def test_init_migrates_legacy_db(tmp_path, monkeypatch):
    monkeypatch.setenv("MINE2DIGEST_DB", str(tmp_path / "legacy.sqlite"))

    conn = db.connect()
    db.init_db(conn)
    conn.execute("DROP TABLE paper_ids")
    conn.execute("PRAGMA user_version = 1")
    conn.close()

    conn = db.connect()
    db.init_db(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    assert conn.execute("SELECT name FROM sqlite_master WHERE name='paper_ids'").fetchone() is not None
    conn.close()


def test_fresh_db_has_reject_reason(isolated, conn):
    assert "reject_reason" in _columns(conn, "evidence_cards")


def test_init_adds_reject_reason_to_legacy_table(tmp_path, monkeypatch):
    path = tmp_path / "legacy3.sqlite"
    monkeypatch.setenv("MINE2DIGEST_DB", str(path))

    raw = sqlite3.connect(path)
    raw.execute(
        "CREATE TABLE evidence_cards ("
        " card_id TEXT PRIMARY KEY, paper_id TEXT NOT NULL, use_type TEXT NOT NULL,"
        " target_section TEXT NOT NULL, claim TEXT, evidence_locator TEXT, source_span TEXT,"
        " method_snippet TEXT, caveat TEXT, confidence REAL, stage TEXT, locator_status TEXT,"
        " original_prediction TEXT, created_at TEXT)"
    )
    raw.execute("PRAGMA user_version = 2")
    raw.commit()
    raw.close()

    conn = db.connect()
    db.init_db(conn)
    assert "reject_reason" in _columns(conn, "evidence_cards")
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    conn.close()
