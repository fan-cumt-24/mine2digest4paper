"""append-only CLI 审计流水（logs/cli.jsonl）。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from . import common


def append(entry: dict, path: str | None = None) -> None:
    if path is None:
        path = os.environ.get("MINE2DIGEST_LOG") or str(common.wpath("logs", "cli.jsonl"))
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
