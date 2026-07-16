from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from endorag.agent.skills.base import SkillResult


CommonContextKind = Literal[
    "question",
    "routing",
    "retrieval",
    "answerability",
    "reasoning",
    "safety",
    "limitation",
]


class CommonContextItem(BaseModel):
    kind: CommonContextKind
    summary: str
    source: str
    importance: Literal["low", "medium", "high"] = "medium"
    evidence_ids: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)


def append_common_context(
    existing: list[dict],
    *,
    source: str,
    kind: CommonContextKind,
    summaries: list[str],
    importance: Literal["low", "medium", "high"] = "medium",
    evidence_ids: list[str] | None = None,
) -> list[dict]:
    items = list(existing or [])
    for summary in summaries:
        clean = " ".join(str(summary).split())
        if not clean:
            continue
        items.append(
            CommonContextItem(
                kind=kind,
                summary=clean[:280],
                source=source,
                importance=importance,
                evidence_ids=evidence_ids or [],
            ).model_dump()
        )
    return _trim_common_context(items)


def append_result_context(
    existing: list[dict],
    result: SkillResult,
    *,
    kind: CommonContextKind,
) -> list[dict]:
    summaries = list(result.context_updates or [])
    if result.limitations:
        summaries.extend(f"Limitation: {item}" for item in result.limitations[:2])
    return append_common_context(
        existing,
        source=result.skill_name,
        kind=kind,
        summaries=summaries,
        importance="high" if result.status in {"partial", "invalid_question", "insufficient_context", "failed"} else "medium",
    )


def render_common_context(
    items: list[dict],
    max_items: int = 12,
    max_chars: int = 1800,
) -> str:
    parsed = [CommonContextItem.model_validate(item) for item in items or []]
    parsed.sort(key=lambda item: {"high": 0, "medium": 1, "low": 2}[item.importance])
    rendered = "\n".join(f"- [{item.kind}] {item.summary}" for item in parsed[:max_items])
    return rendered[:max_chars]


def _trim_common_context(items: list[dict], max_items: int = 30) -> list[dict]:
    parsed = [CommonContextItem.model_validate(item) for item in items]
    parsed.sort(key=lambda item: {"high": 0, "medium": 1, "low": 2}[item.importance])
    return [item.model_dump() for item in parsed[:max_items]]
