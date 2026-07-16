"""LLM-only paper baseline."""

from __future__ import annotations

from typing import Any

from llama_index.core import Settings

from endorag.evaluation.metrics import normalize_mcq_letter
from endorag.evaluation.models import Prediction


class LLMStrategy:
    def answer(
        self,
        question: str,
        context: dict[str, Any] | None = None,
    ) -> Prediction:
        prompt = f"""
Answer the multiple-choice question using your medical knowledge.
Question: {question}
Respond with ONLY the correct letter (a, b, c, d, or e).
Do not write anything else: no words, no punctuation, no spaces, no newlines.
"""
        response = Settings.llm.complete(prompt)
        return Prediction(actual_output=normalize_mcq_letter(str(response)))
