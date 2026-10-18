"""确定性页选择（pdf/select）：分词、预算、top-k、pin、原序。"""

from __future__ import annotations

from mine2digest.pdf import select


def test_tokenize_drops_stopwords_and_short():
    tokens = select.tokenize("The CAT is on a mat, methods!")
    assert "cat" in tokens and "methods" in tokens
    assert "the" not in tokens and "is" not in tokens and "a" not in tokens


def test_char_budget_caps_total():
    pages = {1: "a" * 100, 2: "b" * 100, 3: "c" * 100}
    picked = select.select_pages(pages, "alpha beta", top_k=8, char_budget=150)
    assert sum(len(text) for _, text in picked) <= 150
    assert len(picked) >= 1


def test_top_k_caps_pages():
    pages = {n: "method " * 50 for n in range(1, 11)}
    picked = select.select_pages(pages, "method", top_k=3, char_budget=10**9)
    assert len(picked) == 3


def test_order_is_original_page_order():
    pages = {1: "x", 3: "x", 2: "x"}
    picked = select.select_pages(pages, "x", top_k=3, char_budget=1000)
    assert [n for n, _ in picked] == [1, 2, 3]


def test_pinned_page_included_below_top_k():
    pages = {1: "method method method", 5: "unrelated zzz"}
    picked = select.select_pages(pages, "method", top_k=1, char_budget=1000, pin={5})
    assert [n for n, _ in picked] == [5]


def test_query_terms_drive_ranking():
    pages = {1: "appendix random filler", 2: "baseline comparison table accuracy"}
    picked = select.select_pages(pages, "baseline accuracy", top_k=1, char_budget=1000)
    assert [n for n, _ in picked] == [2]


def test_empty_and_degenerate_inputs():
    assert select.select_pages({}, "x", top_k=3, char_budget=10) == []
    assert select.select_pages({1: "x"}, "x", top_k=0, char_budget=10) == []
    assert select.select_pages({1: "x"}, "x", top_k=3, char_budget=0) == []


def test_resolve_budget_reads_labels():
    labels = {
        "thresholds": {
            "reader_top_k": 4,
            "reader_char_budget": 100,
            "caveat_top_k": 2,
            "caveat_char_budget": 50,
        }
    }
    assert select.resolve_budget(labels, "reader") == (4, 100)
    assert select.resolve_budget(labels, "caveat") == (2, 50)


def test_resolve_budget_fallbacks():
    assert select.resolve_budget({}, "reader") == (8, 12000)
    assert select.resolve_budget({}, "caveat") == (6, 8000)
