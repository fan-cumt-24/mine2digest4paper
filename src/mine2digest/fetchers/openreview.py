"""OpenReview fetcher：按标题搜索笔记，取标题匹配的笔记中的 PDF 链接。"""

from __future__ import annotations

import difflib
import re

from ._http import dest_for, download, get_json

SEARCH_URL = "https://api2.openreview.net/notes/search"
SITE = "https://openreview.net"
TITLE_MATCH_MIN = 0.9


def build_search_params(title: str) -> dict:
    return {"term": title, "content": "all", "group": "all", "source": "forum"}


def _norm_title(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def titles_match(expected: str | None, actual: str | None) -> bool:
    a, b = _norm_title(expected), _norm_title(actual)
    if not a or not b:
        return False
    if a == b:
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= TITLE_MATCH_MIN


def _note_pdf(note: dict) -> str | None:
    content = note.get("content") or {}
    pdf = content.get("pdf")
    if isinstance(pdf, dict):
        pdf = pdf.get("value")
    if not pdf:
        return None
    return SITE + pdf if pdf.startswith("/") else pdf


def _note_title(note: dict) -> str | None:
    content = note.get("content") or {}
    title = content.get("title")
    if isinstance(title, dict):
        title = title.get("value")
    return title if isinstance(title, str) else None


def parse_pdf_url(payload: dict, title: str | None = None) -> str | None:
    notes = payload.get("notes") or payload.get("data") or []
    for note in notes:
        pdf = _note_pdf(note)
        if not pdf:
            continue
        if title is None or titles_match(title, _note_title(note)):
            return pdf
    return None


def try_fetch(paper: dict, dest_dir: str | None = None) -> tuple[bool, str | None, str | None]:
    title = paper.get("title")
    if not title:
        return False, None, "no title"
    try:
        payload = get_json(SEARCH_URL, params=build_search_params(title))
        url = parse_pdf_url(payload, title=title)
        if not url:
            return False, None, "no openreview title match"
        dest = download(url, dest_for(paper, dest_dir))
        return True, str(dest), None
    except Exception as exc:  # noqa: BLE001
        return False, None, str(exc)
