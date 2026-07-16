"""Method-neutral evaluation records."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class Prediction:
    actual_output: str
    retrieval_context: list[Any] = field(default_factory=list)
    flow_diagnostics: dict[str, Any] = field(default_factory=dict)
    routing_category: str | None = None


class EvaluationStrategy(Protocol):
    def answer(
        self,
        question: str,
        context: dict[str, Any] | None = None,
    ) -> Prediction: ...
