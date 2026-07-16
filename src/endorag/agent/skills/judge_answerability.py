from __future__ import annotations

from pydantic import BaseModel, Field

from endorag.retrieval.evidence_models import AnswerabilityAssessment
from endorag.retrieval.evidence_ranking import rank_passages_for_question
from endorag.agent.planning.parameters import QueryParameters
from endorag.agent.skills.base import SkillContext, SkillResult, StatePatch


class JudgeAnswerabilityInput(BaseModel):
    dependency_results: dict = Field(default_factory=dict)


class JudgeAnswerabilitySkill:
    name = "judge_answerability"
    description = "Assess whether retrieved evidence is sufficient to answer."
    input_model = JudgeAnswerabilityInput

    async def run(
        self,
        task_id: str,
        inputs: JudgeAnswerabilityInput,
        context: SkillContext,
        deps,
    ) -> SkillResult:
        passages = _dedupe_passages(
            _passages_from_shared_state(context)
            + _passages_from_dependencies(inputs.dependency_results)
        )
        if not passages:
            assessment = AnswerabilityAssessment(
                sufficient=False,
                confidence="low",
                rationale="No retrieved passages are available.",
                missing_anchors=["guideline evidence for the stem and answer options"],
            )
            return _result(task_id, assessment, status="partial", limitations=["No evidence available."])

        params = QueryParameters.model_validate(context.query_parameters or {})
        question = context.run_metadata.get("question", "")
        ranked_passages = rank_passages_for_question(
            passages,
            anchor_query=question,
            limit=5,
        )
        agent = getattr(deps, "answerability_agent", None)
        if agent is not None:
            prompt = _build_answerability_prompt(
                params,
                ranked_passages,
                question=question,
            )
            try:
                response = await agent.run(prompt, deps=deps)
                assessment = response.output
            except Exception as exc:  # noqa: BLE001
                if hasattr(deps, "log_agent_output"):
                    deps.log_agent_output(
                        {
                            "agent": "answerability",
                            "success": True,
                            "fallback": "deterministic",
                            "warning": repr(exc),
                        }
                    )
                assessment = _deterministic_answerability_fallback(params, passages)
                assessment = _ensure_missing_anchors(params, assessment)
                return _result(
                    task_id,
                    assessment,
                    status="partial",
                    limitations=[
                        f"PydanticAI answerability_agent failed: {exc!r}",
                        "Used deterministic answerability fallback.",
                    ],
                )
        else:
            assessment = _deterministic_answerability_fallback(params, passages)

        assessment = _ensure_missing_anchors(params, assessment)
        assessment = _refine_competing_options(assessment, params)
        return _result(task_id, assessment)


def _passages_from_shared_state(context: SkillContext) -> list[dict]:
    retrieval_state = (context.shared_state or {}).get("retrieval", {})
    passages = retrieval_state.get("accumulated_passages") or []
    return [passage for passage in passages if isinstance(passage, dict)]


def _passages_from_dependencies(dependency_results: dict) -> list[dict]:
    passages: list[dict] = []
    for result in dependency_results.values():
        data = result.get("data", {}) if isinstance(result, dict) else {}
        for passage in data.get("passages", []):
            if isinstance(passage, dict):
                passages.append(passage)
    return passages


def _dedupe_passages(passages: list[dict]) -> list[dict]:
    deduped = []
    seen = set()
    for passage in passages:
        key = passage.get("id") or (passage.get("source"), str(passage.get("text", ""))[:500])
        if key not in seen:
            seen.add(key)
            deduped.append(passage)
    return deduped


def _build_answerability_prompt(
    params: QueryParameters,
    passages: list[dict],
    question: str,
) -> str:
    option_block = "\n".join(f"{option.letter}. {option.text}" for option in params.options)
    evidence_block = "\n\n".join(
        f"[{p.get('id')}] score={p.get('score')} Source: {p.get('source')}\n{p.get('text')}"
        for p in passages[:5]
    )
    return f"""
Question:
{question}

Question mode: {params.mode}
Stem type: {params.stem_type}
Polarity: {params.polarity}
Options:
{option_block}

Hard constraints:
{params.hard_constraints}

Evidence (highest relevance first):
{evidence_block}

Assess whether the evidence is sufficient to answer the full question.
If two or more options remain plausible, set sufficient=false and list missing_anchors as short
search phrases that would distinguish them. Populate closest_competing_options when applicable.
Return only the structured object.
"""


def _deterministic_answerability_fallback(
    params: QueryParameters,
    passages: list[dict],
) -> AnswerabilityAssessment:
    quote = next((str(p.get("text", ""))[:300] for p in passages if p.get("text")), None)
    sufficient = bool(passages)
    return AnswerabilityAssessment(
        sufficient=sufficient,
        confidence="medium" if sufficient else "low",
        rationale="Fallback judged sufficiency from retrieved passage availability.",
        missing_anchors=[] if sufficient else ["additional targeted evidence"],
        supporting_quote=quote,
        evidence_ids=[str(p.get("id")) for p in passages[:5] if p.get("id")],
    )


def _refine_competing_options(
    assessment: AnswerabilityAssessment,
    params: QueryParameters,
) -> AnswerabilityAssessment:
    competitors = [
        str(item).strip()
        for item in assessment.closest_competing_options
        if str(item).strip()
    ]
    if params.mode != "mcq" or len(competitors) < 2:
        return assessment

    if not assessment.missing_anchors:
        assessment.missing_anchors = [competitor[:240] for competitor in competitors[:3]]
    assessment.sufficient = False
    if assessment.confidence == "high":
        assessment.confidence = "medium"
    return assessment


def _ensure_missing_anchors(
    params: QueryParameters,
    assessment: AnswerabilityAssessment,
) -> AnswerabilityAssessment:
    if assessment.sufficient or assessment.missing_anchors:
        return assessment

    anchors = []
    if params.refined_query_seed:
        anchors.append(params.refined_query_seed)
    if params.hard_constraints:
        anchors.extend(params.hard_constraints[:5])

    assessment.missing_anchors = [
        " ".join(str(anchor).split())[:240]
        for anchor in anchors
        if str(anchor).strip()
    ][:6]
    return assessment


def _result(
    task_id: str,
    assessment: AnswerabilityAssessment,
    status: str = "success",
    limitations: list[str] | None = None,
) -> SkillResult:
    summary = "Evidence is sufficient." if assessment.sufficient else "Evidence is not yet sufficient."
    return SkillResult(
        task_id=task_id,
        skill_name="judge_answerability",
        status=status,
        summary=summary,
        data={"answerability": assessment.model_dump()},
        limitations=limitations or ([] if assessment.sufficient else ["More targeted retrieval may be needed."]),
        state_patches=[
            StatePatch(op="set", key="answerability", value=assessment.model_dump(), source="judge_answerability")
        ],
        derived_parameters={"missing_anchors": assessment.missing_anchors} if assessment.missing_anchors else {},
        context_updates=[
            f"Answerability: sufficient={assessment.sufficient}, confidence={assessment.confidence}.",
            f"Missing anchors: {assessment.missing_anchors}" if assessment.missing_anchors else "No missing anchors reported.",
        ],
    )
