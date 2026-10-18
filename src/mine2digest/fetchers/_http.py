"""fetchers 共享的 HTTP 小工具（惰性导入 httpx，离线不依赖）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def get_json(url: str, params: dict | None = None, headers: dict | None = None) -> Any:
    import httpx

    resp = httpx.get(url, params=params, headers=headers, timeout=30.0, follow_redirects=True)
    resp.raise_for_status()
    return resp.json()


def download(url: str, dest: str | Path) -> Path:
    import httpx

    resp = httpx.get(url, timeout=60.0, follow_redirects=True)
    resp.raise_for_status()
    path = Path(dest)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(resp.content)
    return path


def dest_for(paper: dict, dest_dir: str | None) -> Path:
    name = paper.get("paper_id", "paper")
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in str(name))
    return Path(dest_dir or ".") / f"{safe}.pdf"
