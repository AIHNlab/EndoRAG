from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ClinicalQuestionAnalysisOutput(BaseModel):
    """LLM-derived understanding for non-MCQ clinical questions."""

    model_config = ConfigDict(extra="ignore")

    question_intent: str = ""
    key_clinical_anchors: list[str] = Field(default_factory=list)
    open_variables: list[str] = Field(default_factory=list)
    retrieval_queries: list[str] = Field(default_factory=list)
    sufficient_context: bool = True
    reasoning_instructions: list[str] = Field(default_factory=list)
