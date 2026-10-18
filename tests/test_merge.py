"""merge_votes 纯函数边界用例。"""

from mine2digest.merge import merge_votes

LABELS = {
    "priority_weights": {
        "baseline": 1.0,
        "gap_evidence": 1.0,
        "support_claim": 0.8,
        "contradict": 0.8,
        "method_borrow": 0.7,
        "experiment_design": 0.7,
        "style_sample": 0.3,
        "background": 0.1,
    },
    "thresholds": {"vote_confidence_min": 0.5},
}


def use(kind, section, confidence, anchor="a"):
    return {"type": kind, "target_section": section, "anchor": anchor, "confidence": confidence}


def vote(pid, uses, relevant=True):
    return {"paper_id": pid, "topic_relevant": relevant, "uses": uses}


def test_both_high_same_use_is_confirmed():
    a = vote("p1", [use("baseline", "experiments", 0.9)])
    b = vote("p1", [use("baseline", "experiments", 0.8)])
    r = merge_votes(a, b, LABELS)
    assert r["decision"] == "fetch"
    assert r["priority"] == 1.0
    assert len(r["confirmed_uses"]) == 1
    assert r["confirmed_uses"][0]["needs_fulltext_confirm"] is False
    assert r["confirmed_uses"][0]["confidence"] == 0.9
    assert r["disagreement"] == []


def test_one_high_one_low_confirms_high_with_flag():
    a = vote("p1", [use("method_borrow", "method", 0.9)])
    b = vote("p1", [use("method_borrow", "method", 0.3)])
    r = merge_votes(a, b, LABELS)
    assert r["decision"] == "fetch"
    assert len(r["confirmed_uses"]) == 1
    assert r["confirmed_uses"][0]["needs_fulltext_confirm"] is True
    assert r["confirmed_uses"][0]["confidence"] == 0.9


def test_both_low_is_disagreement_not_confirmed():
    a = vote("p1", [use("baseline", "experiments", 0.4)])
    b = vote("p1", [use("baseline", "experiments", 0.2)])
    r = merge_votes(a, b, LABELS)
    assert r["confirmed_uses"] == []
    assert len(r["disagreement"]) == 1
    assert r["decision"] == "low_priority"


def test_single_side_use_goes_to_disagreement():
    a = vote("p1", [use("gap_evidence", "intro", 0.9)])
    b = vote("p1", [], relevant=True)
    r = merge_votes(a, b, LABELS)
    assert r["confirmed_uses"] == []
    assert len(r["disagreement"]) == 1
    assert r["disagreement"][0]["agents"] == ["A"]
    assert r["decision"] == "low_priority"


def test_one_side_confirms_and_other_disagrees_still_fetches():
    a = vote("p1", [use("baseline", "experiments", 0.8), use("support_claim", "intro", 0.6)])
    b = vote("p1", [use("baseline", "experiments", 0.8)])
    r = merge_votes(a, b, LABELS)
    assert r["decision"] == "fetch"
    assert r["priority"] == 1.0
    assert any(d["type"] == "support_claim" for d in r["disagreement"])


def test_both_only_background_is_low_priority():
    a = vote("p1", [use("background", "intro", 0.9)])
    b = vote("p1", [use("background", "intro", 0.9)])
    r = merge_votes(a, b, LABELS)
    assert r["confirmed_uses"][0]["type"] == "background"
    assert r["decision"] == "low_priority"
    assert r["priority"] == 0.0


def test_both_empty_and_irrelevant_is_discard():
    a = vote("p1", [], relevant=False)
    b = vote("p1", [], relevant=False)
    r = merge_votes(a, b, LABELS)
    assert r["decision"] == "discard"
    assert r["confirmed_uses"] == []


def test_both_empty_but_one_relevant_is_low_priority():
    a = vote("p1", [], relevant=True)
    b = vote("p1", [], relevant=False)
    r = merge_votes(a, b, LABELS)
    assert r["decision"] == "low_priority"


def test_priority_sums_multiple_confirmed_types():
    a = vote("p1", [use("baseline", "experiments", 0.9), use("gap_evidence", "intro", 0.9)])
    b = vote("p1", [use("baseline", "experiments", 0.9), use("gap_evidence", "intro", 0.9)])
    r = merge_votes(a, b, LABELS)
    assert r["priority"] == 2.0


def test_different_sections_same_type_both_confirmed():
    a = vote("p1", [use("baseline", "experiments", 0.9), use("baseline", "related_work", 0.9)])
    b = vote("p1", [use("baseline", "experiments", 0.9), use("baseline", "related_work", 0.9)])
    r = merge_votes(a, b, LABELS)
    assert len(r["confirmed_uses"]) == 2
    assert r["priority"] == 2.0
