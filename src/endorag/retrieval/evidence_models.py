from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class EvidencePassage(BaseModel):
    id: str
    text: str
    source: str
    score: float | None = None
    domain: str | None = None
    query: str
    retrieval_round: int
    metadata: dict[str, Any] = Field(default_factory=dict)


class RelevanceAssessment(BaseModel):
    relevant: bool
    rationale: str
    useful_terms: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class AnswerabilityAssessment(BaseModel):
    model_config = ConfigDict(extra="ignore")

    sufficient: bool
    confidence: Literal["low", "medium", "high"]
    rationale: str
    missing_anchors: list[str] = Field(default_factory=list)
    supporting_quote: str | None = None
    closest_competing_options: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class RetrievalPackage(BaseModel):
    passages: list[EvidencePassage] = Field(default_factory=list)
    attempted_queries: list[str] = Field(default_factory=list)
    answerability: AnswerabilityAssessment | None = None
    limitations: list[str] = Field(default_factory=list)
