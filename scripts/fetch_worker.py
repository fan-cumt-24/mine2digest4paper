"""拉取 worker：next-fetch -> 依次 try_fetch -> report-fetch。

联网脚本（需要 httpx 及相应环境变量）。通过 CLI 子进程读写，保证
“CLI 是唯一写入口”。

用法：
    python scripts/fetch_worker.py --limit 5 --rounds 3 --worker w1
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from mine2digest import fetchers  # noqa: E402


def call_cli(args: list[str]) -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [sys.executable, "-m", "mine2digest.cli", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    return json.loads(proc.stdout)


def process(paper: dict, dest_dir: str) -> dict:
    ok, path, source, error = fetchers.try_all(paper, dest_dir=dest_dir)
    if ok:
        return call_cli(
            ["report-fetch", "--paper-id", paper["paper_id"], "--status", "success",
             "--path", str(path), "--source", source or ""]
        )
    return call_cli(
        ["report-fetch", "--paper-id", paper["paper_id"], "--status", "failed",
         "--error", error or "all fetchers failed"]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="fetch worker")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--rounds", type=int, default=1)
    parser.add_argument("--worker", default=None)
    args = parser.parse_args(argv)

    for _ in range(args.rounds):
        claim_args = ["next-fetch", "--limit", str(args.limit)]
        if args.worker:
            claim_args += ["--worker", args.worker]
        body = call_cli(claim_args)
        papers = body.get("papers") or []
        if not papers:
            break
        for paper in papers:
            result = process(paper, tempfile.mkdtemp(prefix="m2d_"))
            sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
