"""pdf 提取与 locator 校验（离线，用 pymupdf 现场生成 PDF）。"""

import pymupdf
import pytest

from mine2digest.pdf import extract, locator


def make_pdf(path, page_texts):
    doc = pymupdf.open()
    for text in page_texts:
        page = doc.new_page()
        page.insert_text((72, 72), text)
    doc.save(str(path))
    doc.close()


@pytest.fixture
def pdf_path(tmp_path):
    path = tmp_path / "paper.pdf"
    make_pdf(path, ["intro baseline method", "results table three", "conclusion"])
    return path


def test_extract_pages_writes_one_file_per_page(pdf_path, tmp_path):
    files = extract.extract_pages(pdf_path, tmp_path / "extracted")
    assert [f.name for f in files] == ["page-1.txt", "page-2.txt", "page-3.txt"]
    texts = extract.load_page_texts(tmp_path / "extracted")
    assert 1 in texts and "baseline" in texts[1]


def test_ensure_extracted_is_idempotent(pdf_path, tmp_path):
    (tmp_path / "paper.pdf").write_bytes(pdf_path.read_bytes())
    first = extract.ensure_extracted(tmp_path)
    assert len(first) == 3
    # 篡改缓存，重跑不应覆盖（幂等）
    (tmp_path / "extracted" / "page-1.txt").write_text("SENTINEL", encoding="utf-8")
    extract.ensure_extracted(tmp_path)
    assert (tmp_path / "extracted" / "page-1.txt").read_text(encoding="utf-8") == "SENTINEL"


def test_ensure_extracted_without_pdf(tmp_path):
    assert extract.ensure_extracted(tmp_path) == []


def test_parse_pages():
    assert locator.parse_pages("p.7, Table 3") == [7]
    assert locator.parse_pages("see pp. 4-6") == [4, 5, 6]
    assert locator.parse_pages("Page 2") == [2]
    assert locator.parse_pages("Table 3 only") == []  # 不误抓表号
    assert locator.parse_pages(None) == []


def test_check_span_verified_on_target_page():
    pages = {1: "unrelated online learning text", 2: "our baseline method achieves ninety percent"}
    status, ratio, matched, page = locator.check_span(
        "baseline method achieves ninety percent", pages, [2], 75
    )
    assert status == "verified"
    assert ratio >= 75
    assert page == 2
    assert "baseline" in matched


def test_check_span_falls_back_to_all_pages():
    pages = {1: "nothing here", 5: "the hidden baseline result"}
    status, ratio, _matched, page = locator.check_span("hidden baseline result", pages, [], 75)
    assert status == "verified"
    assert page == 5


def test_check_span_unverified_below_threshold():
    pages = {1: "completely different content"}
    status, ratio, _matched, _page = locator.check_span("baseline method achieves", pages, [1], 75)
    assert status == "unverified"
    assert ratio < 75
