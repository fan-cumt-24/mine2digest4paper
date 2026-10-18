"""PMC fetcher：DOI -> PMCID（idconv）-> PMC PDF。"""

from __future__ import annotations

from ._http import dest_for, download, get_json

IDCONV_URL = "https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/"


def build_idconv_params(doi: str) -> dict:
    return {"ids": doi, "format": "json"}


def parse_pmcid(payload: dict) -> str | None:
    for record in payload.get("records", []):
        if record.get("pmcid"):
            return record["pmcid"]
    return None


def build_pdf_url(pmcid: str) -> str:
    return f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/pdf/"


def try_fetch(paper: dict, dest_dir: str | None = None) -> tuple[bool, str | None, str | None]:
    doi = (paper.get("ids") or {}).get("doi")
    if not doi:
        return False, None, "no doi"
    try:
        payload = get_json(IDCONV_URL, params=build_idconv_params(doi))
        pmcid = parse_pmcid(payload)
        if not pmcid:
            return False, None, "no pmcid"
        dest = download(build_pdf_url(pmcid), dest_for(paper, dest_dir))
        return True, str(dest), None
    except Exception as exc:  # noqa: BLE001
        return False, None, str(exc)
