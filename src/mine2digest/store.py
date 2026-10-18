"""papers/ 目录管理：slug 生成、PDF 归档、meta.json。"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path

from . import common

PAPERS_DIR = "papers"


def slugify(
    paper_id: str,
    title: str | None = None,
    year: int | str | None = None,
    override: str | None = None,
) -> str:
    """生成 `{year}_{short}_{hash6}` 形式的目录名（无作者信息时用标题前几词）。

    hash 取自 paper_id 前 6 位，保证唯一，避免同名冲突。
    """
    if override:
        return override
    y = str(year) if year else "nd"
    words = re.findall(r"[a-z0-9]+", (title or "").lower())[:4]
    short = "-".join(words) or "paper"
    digest = hashlib.sha1((paper_id or "").encode("utf-8")).hexdigest()[:6]
    return f"{y}_{short}_{digest}"


def papers_root(base: str | Path | None = None) -> Path:
    return Path(base) if base else common.wpath("papers")


def paper_dir(slug: str, base: str | Path | None = None) -> Path:
    return papers_root(base) / slug


def locate(
    paper_id: str,
    title: str | None = None,
    year: int | str | None = None,
    base: str | Path | None = None,
) -> Path:
    """定位某论文的归档目录。先按默认 slug 猜测，失败则扫 meta.json。"""
    guess = paper_dir(slugify(paper_id, title, year), base)
    if (guess / "meta.json").exists() or (guess / "paper.pdf").exists():
        return guess
    root = papers_root(base)
    if root.exists():
        for meta in root.glob("*/meta.json"):
            try:
                data = json.loads(meta.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if data.get("paper_id") == paper_id:
                return meta.parent
    return guess


def archive_pdf(slug: str, src_path: str | Path, base: str | Path | None = None) -> Path:
    """把下载/投放的 PDF 移入 papers/{slug}/paper.pdf，返回目标路径。"""
    dest_dir = paper_dir(slug, base)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "paper.pdf"
    src = Path(src_path)
    if src.resolve() != dest.resolve():
        shutil.move(str(src), str(dest))
    return dest


def write_meta(slug: str, meta: dict, base: str | Path | None = None) -> Path:
    dest_dir = paper_dir(slug, base)
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / "meta.json"
    path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
