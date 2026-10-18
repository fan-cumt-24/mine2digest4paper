"""自动获取 fetcher 链：Unpaywall -> arXiv -> OpenReview -> PMC。

每个 fetcher 暴露统一接口：
    try_fetch(paper: dict, dest_dir: str | None = None) -> (success, path_or_url, error)

`paper` 含 next-fetch 提供的字段，其中 `ids` 是 {id_type: id_value}（doi/openalex/s2/arxiv）。
联网部分惰性导入 httpx；纯解析函数可离线测试。
"""

from __future__ import annotations

from . import arxiv, openreview, pmc, unpaywall

FETCHERS = {
    "unpaywall": unpaywall,
    "arxiv": arxiv,
    "openreview": openreview,
    "pmc": pmc,
}
ORDER = ["unpaywall", "arxiv", "openreview", "pmc"]


def try_all(paper: dict, dest_dir: str | None = None) -> tuple[bool, str | None, str | None, str | None]:
    """按顺序尝试各 fetcher，返回 (success, path_or_url, source, error)。"""
    errors: list[str] = []
    for name in ORDER:
        success, path_or_url, error = FETCHERS[name].try_fetch(paper, dest_dir=dest_dir)
        if success:
            return True, path_or_url, name, None
        errors.append(f"{name}: {error}")
    return False, None, None, "; ".join(errors)
