"""Unpaywall fetcher：按 DOI 查开放获取 PDF。需 UNPAYWALL_EMAIL。"""

from __future__ import annotations

import os

from ._http import dest_for, download, get_json

API_BASE = "https://api.unpaywall.org/v2"


def build_api_url(doi: str) -> str:
    return f"{API_BASE}/{doi}"


def parse_best_oa(payload: dict) -> str | None:
    best = payload.get("best_oa_location") or {}
    return best.get("url_for_pdf") or best.get("url")


def try_fetch(paper: dict, dest_dir: str | None = None) -> tuple[bool, str | None, str | None]:
    doi = (paper.get("ids") or {}).get("doi")
    if not doi:
        return False, None, "no doi"
    email = os.environ.get("UNPAYWALL_EMAIL")
    if not email:
        return False, None, "UNPAYWALL_EMAIL not set"
    try:
        payload = get_json(build_api_url(doi), params={"email": email})
        url = parse_best_oa(payload)
        if not url:
            return False, None, "no oa location"
        dest = download(url, dest_for(paper, dest_dir))
        return True, str(dest), None
    except Exception as exc:  # noqa: BLE001
        return False, None, str(exc)
