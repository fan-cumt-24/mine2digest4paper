"""伪 agent 测试工具：以子进程方式调用 CLI，模拟真实 agent 的调用方式。

刻意不走进程内调用（CliRunner），因为真实 agent 只能看到
`mine2digest` 的 stdout JSON / stdin / 退出码——这里要验证的正是这条契约。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"


def run_cli(args, input_text=None, cwd=None):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, "-m", "mine2digest.cli", *args],
        input=input_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=cwd,
    )


def run_json(args, input_text=None, cwd=None):
    proc = run_cli(args, input_text=input_text, cwd=cwd)
    body = json.loads(proc.stdout)
    return proc, body
