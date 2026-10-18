"""工作区 tmp/ 清理：默认删可再生的中间载荷，保留未知文件。"""

import json
import os
import time
from pathlib import Path

from typer.testing import CliRunner

from mine2digest.cli import app

runner = CliRunner()


def invoke(args):
    return runner.invoke(app, args)


def body(result):
    return json.loads(result.stdout)


def seed(tmp: Path, name: str, content: str = "x", age_hours: float = 0.0):
    path = tmp / name
    path.write_text(content, encoding="utf-8")
    if age_hours:
        when = time.time() - age_hours * 3600
        os.utime(path, (when, when))
    return path


def test_clean_default_removes_payloads_keeps_unknown(isolated):
    tmp = isolated / "tmp"
    tmp.mkdir()
    seed(tmp, "read-abc.json")
    seed(tmp, "caveat_card-1.json")
    seed(tmp, "cards_p1.json")
    seed(tmp, "votes_A.json")
    seed(tmp, "screen_A.json")
    seed(tmp, "seeds_q1.jsonl")
    seed(tmp, "coverage.json")
    seed(tmp, "find_arxiv.py")
    seed(tmp, "notes.txt")

    result = body(invoke(["clean"]))
    assert result["ok"] is True
    assert result["removed"] == 7
    assert result["bytes"] > 0
    remaining = sorted(p.name for p in tmp.iterdir())
    assert remaining == ["find_arxiv.py", "notes.txt"]


def test_clean_dry_run_deletes_nothing(isolated):
    tmp = isolated / "tmp"
    tmp.mkdir()
    seed(tmp, "read-abc.json")
    result = body(invoke(["clean", "--dry-run"]))
    assert result["dry_run"] is True
    assert result["removed"] == 1
    assert (tmp / "read-abc.json").exists()


def test_clean_all_wipes_tmp(isolated):
    tmp = isolated / "tmp"
    tmp.mkdir()
    seed(tmp, "read-abc.json")
    seed(tmp, "find_arxiv.py")
    seed(tmp, "notes.txt")
    result = body(invoke(["clean", "--all"]))
    assert result["removed"] == 3
    assert list(tmp.iterdir()) == []


def test_clean_older_than_filters_by_mtime(isolated):
    tmp = isolated / "tmp"
    tmp.mkdir()
    seed(tmp, "read-old.json", age_hours=48)
    seed(tmp, "read-new.json")
    result = body(invoke(["clean", "--older-than", "24"]))
    assert result["removed"] == 1
    assert (tmp / "read-new.json").exists()
    assert not (tmp / "read-old.json").exists()


def test_clean_custom_pattern_overrides_default(isolated):
    tmp = isolated / "tmp"
    tmp.mkdir()
    seed(tmp, "read-abc.json")
    seed(tmp, "custom.data")
    result = body(invoke(["clean", "--pattern", "*.data"]))
    assert result["removed"] == 1
    assert (tmp / "read-abc.json").exists()
    assert not (tmp / "custom.data").exists()


def test_clean_missing_tmp_is_ok(isolated):
    result = body(invoke(["clean"]))
    assert result["ok"] is True
    assert result["removed"] == 0
