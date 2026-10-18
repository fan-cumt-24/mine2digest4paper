"""merge_votes：双 agent 投票合并的纯函数（核心判断逻辑，好测）。"""

from __future__ import annotations

from typing import Any

FetchDecision = str


def _key(use: dict) -> tuple[str, str]:
    return (use["type"], use["target_section"])


def _use_view(use: dict, confidence: float, needs_fulltext: bool) -> dict:
    return {
        "type": use["type"],
        "target_section": use["target_section"],
        "confidence": confidence,
        "needs_fulltext_confirm": needs_fulltext,
        "anchor": use.get("anchor", ""),
    }


def merge_votes(
    vote_a: dict,
    vote_b: dict,
    labels: dict,
) -> dict[str, Any]:
    """合并 agent A / B 的投票，输出决策、优先级与用途向量。

    规则（见设计规格 4.3 + 决策补充）：
    - 双方都有该用途且 confidence 均 >= min → 确认，needs_fulltext_confirm=False
    - 双方都有、一高一低（低者 < min）        → 确认高置信版本，needs_fulltext_confirm=True
    - 双方都有但都 < min                       → 不确认，进 disagreement
    - 仅一方标注                              → 进 disagreement
    - 存在非 background 的确认用途            → decision=fetch
    - 否则仅有 background 确认 / 有 disagreement / 任一方 topic_relevant → low_priority
    - 否则（双方都无用途且都 topic_relevant=False）→ discard
    """
    weights: dict[str, float] = labels["priority_weights"]
    cmin: float = labels["thresholds"]["vote_confidence_min"]

    uses_a = {_key(u): u for u in vote_a.get("uses", [])}
    uses_b = {_key(u): u for u in vote_b.get("uses", [])}
    keys = list(dict.fromkeys(list(uses_a) + list(uses_b)))

    confirmed: list[dict] = []
    disagreement: list[dict] = []

    for key in keys:
        ua = uses_a.get(key)
        ub = uses_b.get(key)
        if ua and ub:
            ca, cb = ua["confidence"], ub["confidence"]
            if ca >= cmin and cb >= cmin:
                confirmed.append(_use_view(ua, max(ca, cb), False))
            elif ca >= cmin or cb >= cmin:
                high = ua if ca >= cb else ub
                confirmed.append(_use_view(high, high["confidence"], True))
            else:
                disagreement.append(
                    {
                        "type": key[0],
                        "target_section": key[1],
                        "agents": ["A", "B"],
                        "confidences": {"A": ca, "B": cb},
                    }
                )
        else:
            present = ua or ub
            who = "A" if ua else "B"
            disagreement.append(
                {
                    "type": key[0],
                    "target_section": key[1],
                    "agents": [who],
                    "confidences": {who: present["confidence"]},
                }
            )

    non_background = [u for u in confirmed if u["type"] != "background"]

    if non_background:
        decision: FetchDecision = "fetch"
    elif confirmed:  # 仅有 background 被确认
        decision = "low_priority"
    elif disagreement or vote_a.get("topic_relevant") or vote_b.get("topic_relevant"):
        decision = "low_priority"
    else:
        decision = "discard"

    priority = round(sum(weights.get(u["type"], 0.0) for u in non_background), 6)

    return {
        "paper_id": vote_a.get("paper_id") or vote_b.get("paper_id"),
        "decision": decision,
        "priority": priority,
        "confirmed_uses": confirmed,
        "disagreement": disagreement,
        "merged_uses": confirmed,
    }
