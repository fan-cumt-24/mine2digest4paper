import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

LABELS = REPO / "config" / "labels.yaml"


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """隔离环境：临时 DB + 真实 labels，并将 CWD 切到临时目录。"""
    monkeypatch.setenv("MINE2DIGEST_DB", str(tmp_path / "test.sqlite"))
    monkeypatch.setenv("MINE2DIGEST_LABELS", str(LABELS))
    monkeypatch.setenv("MINE2DIGEST_WORKSPACE", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    from mine2digest import db

    conn = db.connect()
    db.init_db(conn)
    conn.close()
    return tmp_path


@pytest.fixture
def conn(isolated):
    from mine2digest import db

    connection = db.connect()
    yield connection
    connection.close()


def add_paper(conn, paper_id, title=None, status="candidate", abstract=None, doi=None):
    from mine2digest import common

    now = common.iso_now()
    conn.execute(
        "INSERT INTO papers (paper_id, doi, title, abstract, status, added_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (paper_id, doi, title or f"title-{paper_id}", abstract, status, now, now),
    )
