from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from endorag.agent.planning.parameters import StemType


Polarity = Literal["standard", "except"]


class MCQStemAnalysisOutput(BaseModel):
    """LLM-derived MCQ understanding; replaces regex stem classification."""

    model_config = ConfigDict(extra="ignore")

    stem_type: StemType = "other"
    polarity: Polarity = "standard"
    question_intent: str = ""
    key_clinical_anchors: list[str] = Field(default_factory=list)
    retrieval_queries: list[str] = Field(default_factory=list)
    reasoning_instructions: list[str] = Field(default_factory=list)
