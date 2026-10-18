"""arXiv fetcher：由 arxiv id 直接构造 PDF URL 下载。"""

from __future__ import annotations

from ._http import dest_for, download


def extract_arxiv_id(paper: dict) -> str | None:
    ids = paper.get("ids") or {}
    if ids.get("arxiv"):
        return ids["arxiv"]
    pid = str(paper.get("paper_id") or "")
    if pid.startswith("arxiv:"):
        return pid.split(":", 1)[1]
    return None


def build_pdf_url(arxiv_id: str) -> str:
    return f"https://arxiv.org/pdf/{arxiv_id}"


def try_fetch(paper: dict, dest_dir: str | None = None) -> tuple[bool, str | None, str | None]:
    arxiv_id = extract_arxiv_id(paper)
    if not arxiv_id:
        return False, None, "no arxiv id"
    try:
        dest = download(build_pdf_url(arxiv_id), dest_for(paper, dest_dir))
        return True, str(dest), None
    except Exception as exc:  # noqa: BLE001
        return False, None, str(exc)
