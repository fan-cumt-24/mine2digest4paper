"""fetcher 纯函数（离线）+ try_all 顺序短路。"""

import json
from pathlib import Path

import mine2digest.fetchers as F
from mine2digest.fetchers import arxiv, openreview, pmc, unpaywall

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_arxiv_id_and_url():
    assert arxiv.extract_arxiv_id({"ids": {"arxiv": "2401.00001"}}) == "2401.00001"
    assert arxiv.extract_arxiv_id({"paper_id": "arxiv:9999.1"}) == "9999.1"
    assert arxiv.extract_arxiv_id({"ids": {}}) is None
    assert arxiv.build_pdf_url("2401.00001") == "https://arxiv.org/pdf/2401.00001"


def test_unpaywall_parse_best_oa():
    assert unpaywall.parse_best_oa(_load("unpaywall.json")) == "https://repo.example/x.pdf"
    assert unpaywall.parse_best_oa(_load("unpaywall_no_oa.json")) is None
    assert unpaywall.build_api_url("10.1/x") == "https://api.unpaywall.org/v2/10.1/x"


def test_openreview_parse_pdf_url():
    assert openreview.parse_pdf_url(_load("openreview.json")) == "https://openreview.net/pdf/abc123.pdf"
    assert openreview.parse_pdf_url({"notes": []}) is None
    assert openreview.parse_pdf_url({"notes": [{"content": {"pdf": {"value": "https://cdn/x.pdf"}}}]}) == "https://cdn/x.pdf"


def test_openreview_titles_match():
    assert openreview.titles_match("Some Paper", "some  paper!")
    assert not openreview.titles_match(
        "Conditional Prompt Learning for Vision-Language Models",
        "Multi-modal Mutual-Guidance Conditional Prompt Learning for Vision-Language Models",
    )
    assert not openreview.titles_match("Some Paper", None)


def test_openreview_parse_pdf_url_requires_title_match():
    payload = {
        "notes": [
            {"content": {"title": {"value": "A Different Paper"}, "pdf": {"value": "/pdf/wrong.pdf"}}},
            {"content": {"title": {"value": "Some Paper"}, "pdf": {"value": "/pdf/right.pdf"}}},
        ]
    }
    assert openreview.parse_pdf_url(payload, title="Some Paper") == "https://openreview.net/pdf/right.pdf"
    assert openreview.parse_pdf_url(payload, title="Unrelated Title") is None


def test_openreview_try_fetch_rejects_title_mismatch(monkeypatch):
    monkeypatch.setattr(
        openreview,
        "get_json",
        lambda *a, **k: {"notes": [{"content": {"title": {"value": "A Different Paper"}, "pdf": {"value": "/pdf/wrong.pdf"}}}]},
    )
    ok, path, err = openreview.try_fetch({"title": "Some Paper"})
    assert (ok, path, err) == (False, None, "no openreview title match")


def test_pmc_parse_and_url():
    assert pmc.parse_pmcid(_load("pmc_idconv.json")) == "PMC1234567"
    assert pmc.parse_pmcid({"records": [{}]}) is None
    assert pmc.build_pdf_url("PMC1234567") == "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC1234567/pdf/"


def test_try_all_short_circuits_in_order(monkeypatch):
    calls = []

    def fake(name, result):
        def _f(paper, dest_dir=None):
            calls.append(name)
            return result

        return _f

    monkeypatch.setattr(unpaywall, "try_fetch", fake("unpaywall", (False, None, "no oa")))
    monkeypatch.setattr(arxiv, "try_fetch", fake("arxiv", (True, "arxiv.pdf", None)))
    monkeypatch.setattr(openreview, "try_fetch", fake("openreview", (True, "or.pdf", None)))
    monkeypatch.setattr(pmc, "try_fetch", fake("pmc", (True, "pmc.pdf", None)))

    ok, path, source, error = F.try_all({"paper_id": "p1"})
    assert (ok, path, source, error) == (True, "arxiv.pdf", "arxiv", None)
    assert calls == ["unpaywall", "arxiv"]  # 成功后不再尝试后续


def test_try_all_reports_all_errors(monkeypatch):
    for name in F.ORDER:
        monkeypatch.setattr(F.FETCHERS[name], "try_fetch", lambda paper, dest_dir=None, n=name: (False, None, f"{n} failed"))
    ok, path, source, error = F.try_all({"paper_id": "p1"})
    assert ok is False
    assert "unpaywall failed" in error and "pmc failed" in error
