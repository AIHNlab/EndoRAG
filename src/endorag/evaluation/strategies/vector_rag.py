"""Single-pass vector RAG paper baseline."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from llama_index.core import Settings

from endorag.evaluation.metrics import normalize_mcq_letter
from endorag.evaluation.models import Prediction
from endorag.retrieval.registry import VectorDbRegistry


class VectorRAGStrategy:
    def __init__(
        self,
        registry: VectorDbRegistry,
        *,
        pinned_category: str | None = None,
        oracle_map_path: str | Path | None = None,
    ) -> None:
        self.registry = registry
        self.pinned_category = pinned_category
        self.oracle_map_path = oracle_map_path

    def answer(
        self,
        question: str,
        context: dict[str, Any] | None = None,
    ) -> Prediction:
        engine, category = self.registry.route(
            question,
            llm=Settings.llm,
            pinned_category=self.pinned_category,
            oracle_map_path=self.oracle_map_path,
        )
        prompt = f"""
Answer the question based ONLY on the retrieval context, do not make any assumptions.
Question: {question}
Respond with ONLY the correct letter (a, b, c, d, or e).
Do not write anything else: no words, no punctuation, no spaces, no newlines.
"""
        response = engine.query(prompt)
        contexts: list[dict[str, Any]] = []
        for node_with_score in getattr(response, "source_nodes", []) or []:
            node = getattr(node_with_score, "node", node_with_score)
            contexts.append(
                {
                    "source": (getattr(node, "metadata", {}) or {}).get(
                        "file_path", "Unknown source"
                    ),
                    "score": getattr(node_with_score, "score", None),
                    "text": node.get_content().strip(),
                }
            )
        return Prediction(
            actual_output=normalize_mcq_letter(getattr(response, "response", response)),
            retrieval_context=contexts,
            routing_category=category,
            flow_diagnostics={"routed_category": category},
        )
