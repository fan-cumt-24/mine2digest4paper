"""密钥 .env：固化在 skill 目录、自动加载、非覆盖已有环境变量。"""

import os
import subprocess
import sys
from pathlib import Path

from mine2digest import common

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"


def test_parse_env_text_handles_comments_quotes_blank():
    text = '# comment\nA=1\n\nB = "two"\nC=\'three\'\nD=\nbadline\n  E = spaced  \n'
    assert common.parse_env_text(text) == [
        ("A", "1"),
        ("B", "two"),
        ("C", "three"),
        ("D", ""),
        ("E", "spaced"),
    ]


def test_load_env_file_sets_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("M2D_TEST_SET", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("M2D_TEST_SET=fromfile\n", encoding="utf-8")
    assert common.load_env_file(env_file) == env_file
    assert os.environ["M2D_TEST_SET"] == "fromfile"


def test_load_env_file_does_not_override(tmp_path, monkeypatch):
    monkeypatch.setenv("M2D_TEST_KEEP", "fromenv")
    env_file = tmp_path / ".env"
    env_file.write_text("M2D_TEST_KEEP=fromfile\n", encoding="utf-8")
    common.load_env_file(env_file)
    assert os.environ["M2D_TEST_KEEP"] == "fromenv"


def test_load_env_file_missing_returns_none(tmp_path):
    assert common.load_env_file(tmp_path / "nope.env") is None


def test_env_path_explicit_override(tmp_path, monkeypatch):
    explicit = tmp_path / "explicit.env"
    explicit.write_text("A=1\n", encoding="utf-8")
    monkeypatch.setenv("MINE2DIGEST_ENV", str(explicit))
    assert common.env_path() == explicit


def test_env_path_finds_skill_dir_by_walking_up(tmp_path, monkeypatch):
    monkeypatch.delenv("MINE2DIGEST_ENV", raising=False)
    skill = tmp_path / ".opencode" / "skills" / "mine2digest"
    skill.mkdir(parents=True)
    env_file = skill / ".env"
    env_file.write_text("UNPAYWALL_EMAIL=a@b.com\n", encoding="utf-8")
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    assert common.env_path() == env_file


def test_cli_autoloads_skill_env(tmp_path):
    skill = tmp_path / ".opencode" / "skills" / "mine2digest"
    skill.mkdir(parents=True)
    (skill / ".env").write_text("M2D_TEST_AUTOLOAD=yes\n", encoding="utf-8")

    env = {k: v for k, v in os.environ.items() if k != "MINE2DIGEST_ENV"}
    env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [sys.executable, "-c", "import mine2digest.cli, os; print(os.environ.get('M2D_TEST_AUTOLOAD'))"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )
    assert proc.stdout.strip() == "yes"
