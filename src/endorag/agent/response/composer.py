from __future__ import annotations

import json

from pydantic import BaseModel, Field

from endorag.agent.planning.models import TaskPlan
from endorag.agent.response.prompts import RESPONSE_COMPOSER_PROMPT, RESPONSE_COMPOSER_USER_TEMPLATE
from endorag.agent.skills.base import SkillResult


class FinalResponse(BaseModel):
    answer_markdown: str
    confidence: float
    followups: list[str] = Field(default_factory=list)
    provenance: list[dict] = Field(default_factory=list)
    task_reports: list[dict] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


async def compose_response(
    question: str,
    plan: TaskPlan,
    results: list[SkillResult],
    deps,
    working_context: str = "",
    shared_execution_state: dict | None = None,
) -> FinalResponse:
    invalid = next(
        (result for result in results if result.status in {"invalid_question", "insufficient_context"}),
        None,
    )
    if invalid:
        return FinalResponse(
            answer_markdown=invalid.summary,
            confidence=0.0,
            provenance=_collect_provenance(results),
            task_reports=[result.model_dump() for result in results],
            warnings=_collect_warnings(results),
        )

    mcq = _extract_mcq_answer(results)
    if mcq:
        return FinalResponse(
            answer_markdown=_format_mcq_answer(mcq),
            confidence=_compute_confidence(results),
            provenance=_collect_provenance(results),
            task_reports=[result.model_dump() for result in results],
            warnings=_collect_warnings(results),
        )

    guidance = _extract_guidance(results)
    if guidance:
        return FinalResponse(
            answer_markdown=guidance,
            confidence=_compute_confidence(results),
            provenance=_collect_provenance(results),
            task_reports=[result.model_dump() for result in results],
            warnings=_collect_warnings(results),
        )

    composer_agent = getattr(deps, "response_composer_agent", None)
    if composer_agent is not None:
        response_prompt = RESPONSE_COMPOSER_USER_TEMPLATE.format(
            question=question,
            goal=plan.goal,
            response_strategy=plan.response_strategy,
            task_reports=json.dumps([result.model_dump() for result in results], indent=2, ensure_ascii=False),
            working_context=working_context or "(none)",
            shared_execution_state=json.dumps(shared_execution_state or {}, indent=2, ensure_ascii=False),
        )
        answer_text = (
            await composer_agent.run_text(
                system_prompt=RESPONSE_COMPOSER_PROMPT,
                user_prompt=response_prompt,
            )
        ).strip()
    else:
        answer_text = "I could not compose a final answer from the available skill reports."

    return FinalResponse(
        answer_markdown=answer_text,
        confidence=_compute_confidence(results),
        provenance=_collect_provenance(results),
        task_reports=[result.model_dump() for result in results],
        warnings=_collect_warnings(results),
    )


def _extract_mcq_answer(results: list[SkillResult]) -> dict | None:
    for result in results:
        answer = (result.data or {}).get("mcq_answer")
        if isinstance(answer, dict):
            return answer
    return None


def _format_mcq_answer(answer: dict) -> str:
    selected = str(answer.get("selected_answer", "")).lower()
    rationale = str(answer.get("rationale", ""))
    quote = answer.get("supporting_quote")
    limitations = answer.get("limitations") or []
    parts = [f"Answer: **{selected}**", rationale]
    if quote:
        parts.append(f"Supporting evidence: \"{quote}\"")
    if limitations:
        parts.append("Limitations: " + "; ".join(str(item) for item in limitations))
    return "\n\n".join(part for part in parts if part)


def _extract_guidance(results: list[SkillResult]) -> str | None:
    for result in results:
        guidance = (result.data or {}).get("guidance")
        if isinstance(guidance, dict) and guidance.get("answer_markdown"):
            return str(guidance["answer_markdown"])
    return None


def _collect_provenance(results: list[SkillResult]) -> list[dict]:
    return [
        {**item, "task_id": result.task_id, "skill_name": result.skill_name}
        for result in results
        for item in result.provenance
    ]


def _collect_warnings(results: list[SkillResult]) -> list[str]:
    warnings = []
    for result in results:
        if result.status in {"partial", "failed"}:
            warnings.append(f"{result.skill_name}: {result.summary}")
        warnings.extend(result.limitations)
    return warnings


def _compute_confidence(results: list[SkillResult]) -> float:
    if not results or any(result.status in {"invalid_question", "insufficient_context"} for result in results):
        return 0.0
    score = 0.35
    score += min(sum(1 for result in results if result.status == "success") * 0.15, 0.45)
    score -= min(sum(1 for result in results if result.status == "failed") * 0.25, 0.5)
    if any(result.evidence for result in results):
        score += 0.15
    if any(
        result.skill_name == "judge_answerability"
        and (result.data or {}).get("answerability", {}).get("sufficient")
        for result in results
    ):
        score += 0.15
    return max(0.0, min(score, 1.0))
