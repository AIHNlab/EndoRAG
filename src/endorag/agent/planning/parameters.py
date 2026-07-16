from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from endorag.agent.planning.question_normalize import (
    StandardizedQuestion,
    standardize_exam_question,
    strip_canonical_options,
)


StemType = Literal["next_step", "management", "except", "diagnosis", "knowledge", "other"]
QuestionMode = Literal["mcq", "clinical_guidance", "unknown"]
Polarity = Literal["standard", "except"]


class MCQOption(BaseModel):
    letter: str
    text: str


class QueryParameters(BaseModel):
    mode: QuestionMode = "unknown"
    stem_type: StemType = "other"
    polarity: Polarity = "standard"
    options: list[MCQOption] = Field(default_factory=list)
    retrieval_queries: list[str] = Field(default_factory=list)
    refined_query_seed: str = ""
    keywords: list[str] = Field(default_factory=list)
    suspected_domain: str | None = None
    hard_constraints: list[str] = Field(default_factory=list)
    soft_preferences: list[str] = Field(default_factory=list)
    open_variables: list[str] = Field(default_factory=list)
    working_context_seed: str = ""
    constraints_explicit: dict[str, Any] = Field(default_factory=dict)
    provenance: list[dict[str, Any]] = Field(default_factory=list)

    model_config = {"extra": "allow"}


def extract_query_parameters(question: str) -> QueryParameters:
    """Structural parse only: options, mode, and text seeds.

    Input is standardized to canonical MCQ layout first. Semantic fields
    (stem type, polarity, constraints, retrieval queries) are filled later
    by LLM skills such as analyze_mcq_stem / analyze_clinical_question.
    """
    standardized = standardize_exam_question(question)
    return query_parameters_from_standardized(standardized)


def query_parameters_from_standardized(standardized: StandardizedQuestion) -> QueryParameters:
    options = [
        MCQOption(letter=option.letter, text=option.text)
        for option in standardized.options
    ]
    canonical = standardized.canonical
    polarity = detect_question_polarity(canonical)

    if len(options) >= 2:
        mode: QuestionMode = "mcq"
    elif canonical:
        mode = "clinical_guidance"
    else:
        mode = "unknown"

    return QueryParameters(
        mode=mode,
        options=options,
        stem_type="except" if polarity == "except" else "other",
        polarity=polarity,
        refined_query_seed=_build_refined_query_seed(canonical, options),
        working_context_seed=strip_options(canonical)[:1600],
        provenance=[
            {
                "source": "structural_parser",
                "input_format": standardized.format,
                "option_count": len(options),
            }
        ],
    )


def strip_options(question: str) -> str:
    return strip_canonical_options(question)


def detect_question_polarity(question: str) -> Polarity:
    """Detect explicit negative MCQ stems before LLM stem analysis runs."""
    text = strip_options(question).lower()
    if re.search(r"\b(except|incorrect|not correct|false|least likely|unlikely)\b", text):
        return "except"
    if re.search(r"\b(which|what)\b.{0,80}\bnot\b", text):
        return "except"
    return "standard"


def _build_refined_query_seed(question: str, options: list[MCQOption]) -> str:
    stem = strip_options(question)
    seed = " ".join(stem.split())
    if len(seed) > 900:
        seed = seed[:900]
    if options:
        option_terms = " ".join(option.text for option in options)
        return f"{seed} {option_terms}"[:1200]
    return seed
