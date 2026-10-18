"""工作区 `tmp/` 清理：删除可再生的中间载荷，保留未知文件（除非 --all）。

默认只删已知的中间产物 glob（阅读/反证载荷、筛选/投票/建卡的 agent 交接文件、种子等），
不动 `*.py`、说明文本等来历不明的文件；`--all` 才清空整个 `tmp/`。
"""

from __future__ import annotations

import time
from pathlib import Path

from .. import common

DEFAULT_PATTERNS = (
    "read*.json",      # read-<id>.json / read_batch*.json / reading_left*.json
    "caveat*.json",    # caveat-<id>.json / caveat_card-*.json / caveat_queue.json
    "cards_*.json",    # 建卡 agent 交接文件
    "cav_*.json",      # 反证 agent 交接文件
    "screen_*.json",   # 筛选载荷
    "votes_*.json",    # 投票文件
    "audit*.json",     # audit.json / audit_sample.json / audit_card-*.json
    "coverage.json",
    "seeds_*.jsonl",   # 发现阶段种子（已入库，可重生成）
)


def clean(
    patterns: list[str] | None = None,
    all_files: bool = False,
    older_than_hours: float | None = None,
    dry_run: bool = False,
    tmp_dir: str | Path | None = None,
) -> dict:
    tmp = Path(tmp_dir) if tmp_dir else common.wpath("tmp")
    if not tmp.exists():
        return common.ok(path=str(tmp), removed=0, bytes=0, files=[], dry_run=dry_run)

    if all_files:
        candidates = [p for p in tmp.rglob("*") if p.is_file()]
    else:
        globs = patterns or list(DEFAULT_PATTERNS)
        found: list[Path] = []
        for pattern in globs:
            found.extend(tmp.glob(pattern))
        candidates = [p for p in found if p.is_file()]

    seen: set[Path] = set()
    files: list[Path] = []
    for path in candidates:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        files.append(path)

    if older_than_hours is not None:
        cutoff = time.time() - older_than_hours * 3600.0
        files = [p for p in files if p.stat().st_mtime < cutoff]

    total = sum(p.stat().st_size for p in files)
    if not dry_run:
        for path in files:
            try:
                path.unlink()
            except OSError:
                pass

    return common.ok(
        path=str(tmp),
        removed=len(files),
        bytes=total,
        files=[str(p) for p in files],
        dry_run=dry_run,
    )
