"""载荷离线化：next-reading / next-caveat 写文件 + 清单，页文本不进 stdout，按论文去重。"""

from __future__ import annotations

import json
from pathlib import Path

import pymupdf
from conftest import add_paper
from typer.testing import CliRunner

from mine2digest import store
from mine2digest.cli import app
from mine2digest.common import iso_now

runner = CliRunner()


def invoke(args):
    return runner.invoke(app, args)


def body(result):
    return json.loads(result.stdout)


def make_fetched(conn, pid, pages, title="Paper One"):
    slug = store.slugify(pid, title, None)
    pdir = store.paper_dir(slug)
    pdir.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()
    for text in pages:
        doc.new_page().insert_text((72, 72), text)
    doc.save(str(pdir / "paper.pdf"))
    doc.close()
    store.write_meta(slug, {"paper_id": pid, "title": title})
    add_paper(conn, pid, title=title, status="fetched")
    conn.execute(
        "INSERT INTO confirmed_uses (paper_id, use_type, target_section, confidence, needs_fulltext_confirm)"
        " VALUES (?, 'baseline', 'experiments', 0.9, 0)",
        (pid,),
    )
    return slug


def add_card(conn, card_id, paper_id="p1"):
    conn.execute(
        "INSERT INTO evidence_cards"
        " (card_id, paper_id, use_type, target_section, claim, source_span, confidence,"
        "  stage, locator_status, created_at)"
        " VALUES (?, ?, 'baseline', 'experiments', 'claim', 'span', 0.9, 'fulltext_confirmed', 'verified', ?)",
        (card_id, paper_id, iso_now()),
    )


def test_next_reading_offloads_and_respects_budget(isolated, conn):
    big = "method baseline experiment " * 2000  # ~52k chars, forces truncation
    make_fetched(conn, "p1", [big, "appendix unrelated text"])
    result = body(invoke(["next-reading", "--out-dir", "payloads", "--char-budget", "5000"]))
    assert result["ok"] is True
    assert result["count"] == 1
    assert "text" not in json.dumps(result)  # 页文本不进 stdout
    item = result["items"][0]
    assert item["paper_id"] == "p1"
    data = json.loads(Path(item["path"]).read_text(encoding="utf-8"))
    assert data["paper_id"] == "p1"
    assert sum(len(page["text"]) for page in data["pages"]) <= 5000


def test_next_reading_inline_default_still_works(isolated, conn):
    make_fetched(conn, "p1", ["baseline method accuracy", "ablation"])
    result = body(invoke(["next-reading"]))
    assert result["papers"][0]["pages"]


def test_next_caveat_dedups_pages_per_paper(isolated, conn):
    make_fetched(conn, "p1", ["baseline method accuracy and more text"])
    add_card(conn, "c1")
    add_card(conn, "c2")
    result = body(invoke(["next-caveat", "--out-dir", "payloads"]))
    assert result["count"] == 2
    assert result["item_count"] == 2
    assert "text" not in json.dumps(result)
    paths = {item["path"] for item in result["items"]}
    assert len(paths) == 1  # 同一篇 -> 同一文件
    data = json.loads(Path(next(iter(paths))).read_text(encoding="utf-8"))
    assert len(data["cards"]) == 2
    assert data["pages"]


def test_top_k_flag_limits_pages(isolated, conn):
    pages = [f"method section number {n} " * 100 for n in range(1, 6)]
    make_fetched(conn, "p1", pages)
    result = body(invoke(["next-reading", "--out-dir", "payloads", "--top-k", "1", "--char-budget", "100000"]))
    data = json.loads(Path(result["items"][0]["path"]).read_text(encoding="utf-8"))
    assert len(data["pages"]) == 1
