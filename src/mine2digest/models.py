"""pydantic 模型：投票输入的结构校验。

`extra="forbid"`：agent 输出出现未知字段时直接拒绝，不静默忽略，
与设计规格“非法直接拒绝（不静默纠正）”一致。
"""

from __future__ import annotations

from typing import List

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Use(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    target_section: str
    anchor: str = ""
    confidence: float = Field(ge=0.0, le=1.0)


class Vote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paper_id: str
    topic_relevant: bool = False
    uses: List[Use] = Field(default_factory=list)

    @field_validator("uses")
    @classmethod
    def _no_duplicate_uses(cls, uses: List[Use]) -> List[Use]:
        seen = set()
        for use in uses:
            key = (use.type, use.target_section)
            if key in seen:
                raise ValueError(f"duplicate use (type, target_section): {key}")
            seen.add(key)
        return uses


class VoteFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    votes: List[Vote] = Field(default_factory=list)


class Card(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paper_id: str
    use_type: str
    target_section: str
    claim: str
    source_span: str
    evidence_locator: str = ""
    method_snippet: str | None = None
    caveat: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    stage: str = "fulltext_confirmed"
    original_prediction: dict | None = None
    card_id: str | None = None


class CardFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cards: List[Card] = Field(default_factory=list)


class Caveat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    caveat: str
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class AuditResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    card_id: str
    passes: bool
    reason: str | None = None


class AuditFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    results: List[AuditResult] = Field(default_factory=list)
