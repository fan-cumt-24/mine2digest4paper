"""Semantic Scholar 一跳引用扩展 -> candidates / relations JSONL。

保留规则：边的 `isInfluential` 为真，或 `intents` 命中 {methodology, result}
（规格 A2）。产出两份 JSONL，由用户 pipe 给 CLI：
    python scripts/s2_expand.py --seed S2ID --out-prefix out
    mine2digest add-candidates --source s2 < out.candidates.jsonl
    mine2digest add-relations < out.relations.jsonl

联网用法需 S2_API_KEY（可选）。离线可用 --from-file 喂已抓取的响应。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

INTENTS_KEEP = {"methodology", "result"}
BASE_URL = "https://api.semanticscholar.org/graph/v1/paper"
FIELDS = "paperId,title,abstract,year,venue,externalIds,isInfluential,intents,contexts"


def qualifies(edge: dict) -> bool:
    if edge.get("isInfluential"):
        return True
    return bool(set(edge.get("intents") or []) & INTENTS_KEEP)


def parse_s2_response(data: dict, seed_id: str, direction: str = "references") -> tuple[list[dict], list[dict]]:
    """把 S2 references/citations 响应解析为 (candidates, relations)。

    direction="references"：seed 引用对方（seed -> other）
    direction="citations" ：对方引用 seed（other -> seed）
    仅保留 qualify 的边，且只把这些边涉及的论文作为候选。
    """
    candidates: list[dict] = []
    relations: list[dict] = []
    node_key = "citedPaper" if direction == "references" else "citingPaper"

    for edge in data.get("data", []):
        if not qualifies(edge):
            continue
        paper = edge.get(node_key) or {}
        sid = paper.get("paperId")
        if not sid:
            continue
        if direction == "references":
            src, dst = seed_id, sid
        else:
            src, dst = sid, seed_id
        relations.append(
            {
                "src_id": src,
                "dst_id": dst,
                "relation": "cites",
                "is_influential": bool(edge.get("isInfluential")),
                "contexts": edge.get("contexts") or [],
            }
        )
        candidates.append(
            {
                "s2_id": sid,
                "doi": (paper.get("externalIds") or {}).get("DOI"),
                "title": paper.get("title"),
                "abstract": paper.get("abstract"),
                "year": paper.get("year"),
                "venue": paper.get("venue"),
                "source": "s2",
            }
        )
    return candidates, relations


def fetch_one_hop(
    seed_id: str,
    direction: str = "references",
    limit: int = 100,
    api_key: str | None = None,
    retries: int = 3,
) -> dict:
    """联网抓取（opt-in）。S2 共享池无 key 时易 429，这里做退避重试。"""
    import time

    import httpx

    headers = {"x-api-key": api_key} if api_key else {}
    url = f"{BASE_URL}/{seed_id}/{direction}"
    params = {"fields": FIELDS, "limit": limit}
    last: Exception | None = None
    for attempt in range(retries):
        resp = httpx.get(url, params=params, headers=headers, timeout=30.0)
        if resp.status_code == 429 or resp.status_code >= 500:
            last = httpx.HTTPStatusError(
                f"{resp.status_code} for {url}", request=resp.request, response=resp
            )
            time.sleep(2 * (attempt + 1))
            continue
        resp.raise_for_status()
        return resp.json()
    assert last is not None
    raise last


def _write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="S2 one-hop citation expansion -> JSONL")
    parser.add_argument("--seed", action="append", required=True, help="S2 paperId 或 DOI:/ARXIV: 前缀 id")
    parser.add_argument("--direction", choices=["references", "citations", "both"], default="both")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--from-file", default=None, help="离线：读取单个 S2 响应 JSON")
    parser.add_argument("--out-prefix", default="s2_out", help="输出前缀，生成 <prefix>.candidates.jsonl / .relations.jsonl")
    args = parser.parse_args(argv)

    api_key = os.environ.get("S2_API_KEY")
    directions = ["references", "citations"] if args.direction == "both" else [args.direction]

    all_candidates: list[dict] = []
    all_relations: list[dict] = []
    for seed in args.seed:
        for direction in directions:
            if args.from_file:
                data = json.loads(Path(args.from_file).read_text(encoding="utf-8"))
            else:
                data = fetch_one_hop(seed, direction, args.limit, api_key)
            cands, rels = parse_s2_response(data, seed, direction)
            all_candidates.extend(cands)
            all_relations.extend(rels)

    prefix = Path(args.out_prefix)
    _write(prefix.with_suffix(".candidates.jsonl"), all_candidates)
    _write(prefix.with_suffix(".relations.jsonl"), all_relations)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
