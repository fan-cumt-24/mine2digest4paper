"""工作区解析：默认 <cwd>/m2d、标记向上查找、内置资产回退、产物落工作区。"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from mine2digest import common
from mine2digest.cli import app

WS_ENV = ("MINE2DIGEST_WORKSPACE", "MINE2DIGEST_DB", "MINE2DIGEST_LABELS", "MINE2DIGEST_ASSETS")


@pytest.fixture
def clean_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for key in WS_ENV:
        monkeypatch.delenv(key, raising=False)
    return tmp_path


def test_default_workspace_is_cwd_m2d(clean_env):
    assert common.workspace() == clean_env / "m2d"
    assert common.wpath("db", "mine2digest.sqlite") == clean_env / "m2d" / "db" / "mine2digest.sqlite"


def test_labels_fall_back_to_packaged_default(clean_env):
    assert not (clean_env / "config" / "labels.yaml").exists()
    assert common.labels_path() == common.PACKAGE_DATA / "labels.yaml"
    assert "use_types" in common.load_labels()


def test_env_overrides_workspace(clean_env, monkeypatch):
    other = clean_env / "elsewhere"
    other.mkdir()
    monkeypatch.setenv("MINE2DIGEST_WORKSPACE", str(other))
    assert common.workspace() == other


def test_marked_workspace_found_by_walk_up(clean_env, monkeypatch):
    project = clean_env / "proj"
    (project / "m2d").mkdir(parents=True)
    (project / "m2d" / common.WORKSPACE_MARKER).write_text("x", encoding="utf-8")
    nested = project / "sub" / "deep"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    assert common.workspace() == project / "m2d"


def test_init_builds_workspace_skeleton(clean_env):
    runner = CliRunner()
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.output
    ws = clean_env / "m2d"
    assert (ws / common.WORKSPACE_MARKER).exists()
    assert (ws / "db" / "mine2digest.sqlite").exists()
    assert (ws / "config" / "experiment_brief.md").exists()
    for sub in ("logs", "papers", "exports", "drafts", "outputs", "inbox"):
        assert (ws / sub).is_dir()


def test_export_writes_under_workspace(clean_env):
    import json

    from mine2digest import db

    runner = CliRunner()
    assert runner.invoke(app, ["init"]).exit_code == 0

    ws = clean_env / "m2d"
    conn = db.connect(str(ws / "db" / "mine2digest.sqlite"))
    now = common.iso_now()
    conn.execute(
        "INSERT INTO papers (paper_id, title, status, added_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        ("P1", "t", "candidate", now, now),
    )
    conn.close()

    assert runner.invoke(app, ["export"]).exit_code == 0
    assert (ws / "exports" / "coverage_matrix.json").exists()
    assert not (clean_env / "exports").exists()
    assert json.loads((ws / "exports" / "fail_list.json").read_text(encoding="utf-8")) == []


def test_init_workspace_flag(clean_env):
    target = clean_env / "custom_ws"
    runner = CliRunner()
    result = runner.invoke(app, ["init", "--workspace", str(target)])
    assert result.exit_code == 0, result.output
    assert (target / common.WORKSPACE_MARKER).exists()
    assert (target / "db" / "mine2digest.sqlite").exists()
    assert not (clean_env / "m2d").exists()
