"""OpenAlex 种子发现：检索 -> 规范化 JSONL。

输出可直接 pipe 给 `mine2digest add-candidates`。

联网用法：
    python scripts/openalex_seed.py --query "retrieval augmented generation" --limit 50 --out seeds.jsonl
离线用法（对已抓取的原始 JSON 做规范化）：
    python scripts/openalex_seed.py --from-file raw_openalex.json

环境变量：OPENALEX_API_KEY（可选，提高限速）
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mine2digest import common  # noqa: E402

common.load_env_file()  # 自动加载 skill 目录 .env（OPENALEX_API_KEY 等）

API_URL = "https://api.openalex.org/works"


def build_params(query: str, limit: int, filters: str | None = None, mailto: str | None = None) -> dict:
    params: dict[str, Any] = {"search": query, "per-page": limit}
    if filters:
        params["filter"] = filters
    if mailto:
        params["mailto"] = mailto
    return params


def _short_id(openalex_url: str | None) -> str | None:
    if not openalex_url:
        return None
    return openalex_url.rstrip("/").rsplit("/", 1)[-1]


def normalize_work(work: dict) -> dict:
    """把 OpenAlex work 记录规范化为 add-candidates 的 JSONL 行。"""
    location = work.get("primary_location") or {}
    source = (location.get("source") or {}).get("display_name")
    oid = _short_id(work.get("id"))
    return {
        "paper_id": oid,
        "openalex_id": oid,
        "doi": work.get("doi"),
        "title": work.get("title") or work.get("display_name"),
        "abstract": common.reconstruct_abstract(work.get("abstract_inverted_index")),
        "year": work.get("publication_year"),
        "venue": source,
        "source": "openalex",
    }


def fetch_works(params: dict, api_key: str | None = None, retries: int = 3) -> list[dict]:
    """联网抓取（opt-in）；httpx 惰性导入，离线路径不依赖它。遇 429/5xx 退避重试。"""
    import time

    import httpx

    query = dict(params)
    if api_key:
        query["api_key"] = api_key
    last: Exception | None = None
    for attempt in range(retries):
        resp = httpx.get(API_URL, params=query, timeout=30.0)
        if resp.status_code == 429 or resp.status_code >= 500:
            last = httpx.HTTPStatusError(
                f"{resp.status_code} for {API_URL}", request=resp.request, response=resp
            )
            time.sleep(2 * (attempt + 1))
            continue
        resp.raise_for_status()
        return resp.json().get("results", [])
    assert last is not None
    raise last


def _load(from_file: str | None, params: dict, api_key: str | None) -> list[dict]:
    if from_file:
        data = json.loads(Path(from_file).read_text(encoding="utf-8"))
        return data.get("results", data) if isinstance(data, dict) else data
    return fetch_works(params, api_key)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="OpenAlex seed discovery -> normalized JSONL")
    parser.add_argument("--query", default="")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--filter", default=None)
    parser.add_argument("--mailto", default=None)
    parser.add_argument("--from-file", default=None, help="离线：读取原始 OpenAlex JSON")
    parser.add_argument("--out", default=None, help="输出文件；缺省 stdout")
    args = parser.parse_args(argv)

    mailto = args.mailto or os.environ.get("OPENALEX_MAILTO") or os.environ.get("UNPAYWALL_EMAIL")
    params = build_params(args.query, args.limit, args.filter, mailto)
    works = _load(args.from_file, params, os.environ.get("OPENALEX_API_KEY"))
    records = [normalize_work(w) for w in works]

    text = "\n".join(json.dumps(r, ensure_ascii=False) for r in records)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    else:
        sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
