"""通用工具：JSON 契约、labels 加载、时间戳、去重规范化。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

PACKAGE_DATA = Path(__file__).resolve().parent / "data"
WORKSPACE_DIRNAME = "m2d"
WORKSPACE_MARKER = ".mine2digest"
DEFAULT_LABELS = "config/labels.yaml"


def _find_marked_workspace(start: Path) -> Path | None:
    """从 start 起向上找最近的、带标记的 m2d/ 工作区。"""
    for base in (start, *start.parents):
        cand = base / WORKSPACE_DIRNAME
        if (cand / WORKSPACE_MARKER).exists():
            return cand
    return None


def workspace() -> Path:
    """运行时数据/产物根：env > 带标记的 m2d/（向上查找）> <cwd>/m2d。"""
    env = os.environ.get("MINE2DIGEST_WORKSPACE")
    if env:
        return Path(env)
    found = _find_marked_workspace(Path.cwd())
    if found is not None:
        return found
    return Path.cwd() / WORKSPACE_DIRNAME


def wpath(*parts: str) -> Path:
    """工作区内的路径。"""
    return workspace().joinpath(*parts)


def assets_root() -> Path:
    """只读资产根（labels/prompts/schema）：env MINE2DIGEST_ASSETS > 包内置 data/。"""
    env = os.environ.get("MINE2DIGEST_ASSETS")
    return Path(env) if env else PACKAGE_DATA


def iso_now() -> str:
    """UTC ISO-8601 时间戳（秒级）。"""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# --- 密钥 / .env ---
#
# 密钥**固化在 skill 自身目录**的 `.env`（项目：`<repo>/.opencode/skills/mine2digest/.env`；
# 全局：`~/.config/opencode/skills/mine2digest/.env`），**绝不放进工作区**。CLI/脚本启动时
# 自动加载（非覆盖已有进程变量）；`MINE2DIGEST_ENV` 可显式指定文件。


def _skill_env_candidates() -> list[Path]:
    candidates: list[Path] = []
    cwd = Path.cwd()
    for base in (cwd, *cwd.parents):
        candidates.append(base / ".opencode" / "skills" / "mine2digest" / ".env")
    try:
        repo = PACKAGE_DATA.parents[2]
        candidates.append(repo / ".opencode" / "skills" / "mine2digest" / ".env")
        candidates.append(repo / "config" / ".env")  # 旧位置，向后兼容
    except IndexError:  # pragma: no cover - 只在异常安装布局下发生
        pass
    candidates.append(Path.home() / ".config" / "opencode" / "skills" / "mine2digest" / ".env")
    return candidates


def env_path() -> Path | None:
    """解析 .env 文件：`MINE2DIGEST_ENV` > skill 目录 `.env`（向上查找）> 全局 skill > 旧位置。"""
    explicit = os.environ.get("MINE2DIGEST_ENV")
    if explicit:
        return Path(explicit)
    seen: set[Path] = set()
    for candidate in _skill_env_candidates():
        key = candidate.resolve()
        if key in seen:
            continue
        seen.add(key)
        if candidate.exists():
            return candidate
    return None


def parse_env_text(text: str) -> list[tuple[str, str]]:
    """解析简单 KEY=VALUE（忽略空行/# 注释，去除首尾引号）。"""
    pairs: list[tuple[str, str]] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        value = value.strip().strip('"').strip("'")
        pairs.append((key, value))
    return pairs


def load_env_file(path: str | Path | None = None) -> Path | None:
    """加载 .env（非覆盖：已存在的环境变量优先）；返回加载的文件路径，未找到返回 None。"""
    target = Path(path) if path else env_path()
    if target is None or not target.is_file():
        return None
    try:
        text = target.read_text(encoding="utf-8")
    except OSError:
        return None
    for key, value in parse_env_text(text):
        os.environ.setdefault(key, value)
    return target


def ok(**kw: Any) -> dict:
    return {"ok": True, **kw}


def err(reason: str, **kw: Any) -> dict:
    return {"ok": False, "reason": reason, **kw}


def labels_path(path: str | None = None) -> Path:
    """labels 解析：显式 path > MINE2DIGEST_LABELS > <workspace>/config/labels.yaml > 包内置默认。"""
    if path:
        return Path(path)
    env = os.environ.get("MINE2DIGEST_LABELS")
    if env:
        return Path(env)
    local = wpath("config", "labels.yaml")
    if local.exists():
        return local
    return PACKAGE_DATA / "labels.yaml"


def load_labels(path: str | None = None) -> dict:
    p = labels_path(path)
    with p.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def read_input(file: str | None) -> str:
    """从 `--file` 或 stdin 读取原始输入文本。"""
    if file:
        return Path(file).read_text(encoding="utf-8")
    return sys.stdin.read()


def parse_json(text: str) -> Any:
    text = (text or "").strip()
    if not text:
        raise ValueError("empty input")
    return json.loads(text)


_DOI_PREFIXES = (
    "https://doi.org/",
    "http://doi.org/",
    "https://dx.doi.org/",
    "http://dx.doi.org/",
    "doi:",
)


def normalize_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    d = doi.strip().lower()
    for prefix in _DOI_PREFIXES:
        if d.startswith(prefix):
            d = d[len(prefix):]
            break
    d = d.strip()
    return d or None


def normalize_title(title: str) -> str:
    """标题规范化后取 sha1，作为去重键的兜底。"""
    t = re.sub(r"[^0-9a-z]+", " ", (title or "").lower())
    t = " ".join(t.split())
    return hashlib.sha1(t.encode("utf-8")).hexdigest()


def canonical_paper_id(rec: dict) -> str:
    """规范 paper_id：显式 paper_id > openalex > s2 > doi:... > local:sha1(title)。"""
    if rec.get("paper_id"):
        return rec["paper_id"]
    if rec.get("openalex_id"):
        return rec["openalex_id"]
    if rec.get("s2_id"):
        return rec["s2_id"]
    doi = normalize_doi(rec.get("doi"))
    if doi:
        return f"doi:{doi}"
    return "local:" + normalize_title(rec.get("title", ""))


def candidate_ids(rec: dict) -> list[tuple[str, str]]:
    """抽取用于去重的 (id_type, id_value) 列表，含 title 兜底。"""
    ids: list[tuple[str, str]] = []
    doi = normalize_doi(rec.get("doi"))
    if doi:
        ids.append(("doi", doi))
    if rec.get("openalex_id"):
        ids.append(("openalex", rec["openalex_id"]))
    if rec.get("s2_id"):
        ids.append(("s2", rec["s2_id"]))
    if rec.get("arxiv_id"):
        ids.append(("arxiv", rec["arxiv_id"]))
    if rec.get("title"):
        ids.append(("title", normalize_title(rec["title"])))
    return ids


def reconstruct_abstract(inverted_index: dict | None) -> str | None:
    """把 OpenAlex 的 abstract_inverted_index 还原成文本。"""
    if not inverted_index:
        return None
    slots: list[tuple[int, str]] = []
    for word, positions in inverted_index.items():
        for pos in positions:
            slots.append((pos, word))
    if not slots:
        return None
    slots.sort()
    out: list[str] = [""] * (slots[-1][0] + 1)
    for pos, word in slots:
        out[pos] = word
    return " ".join(w for w in out if w)
