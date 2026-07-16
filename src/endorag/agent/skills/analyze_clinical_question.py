from __future__ import annotations

from pydantic import BaseModel, Field

from endorag.agent.planning.parameters import QueryParameters, strip_options
from endorag.agent.skills.base import SkillContext, SkillResult, StatePatch
from endorag.agent.skills.clinical_question_models import ClinicalQuestionAnalysisOutput


class AnalyzeClinicalQuestionInput(BaseModel):
    refined_query_seed: str = ""


class AnalyzeClinicalQuestionSkill:
    name = "analyze_clinical_question"
    description = "Analyze a non-MCQ clinical question via structured LLM output."
    input_model = AnalyzeClinicalQuestionInput

    async def run(
        self,
        task_id: str,
        inputs: AnalyzeClinicalQuestionInput,
        context: SkillContext,
        deps,
    ) -> SkillResult:
        question = context.run_metadata.get("question", "")
        params = QueryParameters.model_validate(context.query_parameters or {})
        analysis = await _analyze_with_agent(question, params, deps)
        if analysis is None:
            analysis = _minimal_fallback(question, inputs.refined_query_seed)

        if not analysis.sufficient_context:
            return SkillResult(
                task_id=task_id,
                skill_name=self.name,
                status="insufficient_context",
                summary="Clinical question lacks context required for a reliable answer.",
                data={"clinical_question_analysis": analysis.model_dump()},
                limitations=[
                    f"Missing detail: {item}" for item in analysis.open_variables
                ],
                state_patches=[
                    StatePatch(
                        op="set",
                        key="clinical.question_analysis",
                        value=analysis.model_dump(),
                        source=self.name,
                    )
                ],
                derived_parameters={
                    "open_variables": analysis.open_variables,
                    "retrieval_queries": analysis.retrieval_queries,
                    "hard_constraints": analysis.key_clinical_anchors,
                },
                context_updates=[
                    f"Insufficient clinical context. Open variables: {', '.join(analysis.open_variables) or 'unspecified'}."
                ],
            )

        return SkillResult(
            task_id=task_id,
            skill_name=self.name,
            status="success",
            summary="Analyzed clinical guidance question.",
            data={"clinical_question_analysis": analysis.model_dump()},
            state_patches=[
                StatePatch(
                    op="set",
                    key="clinical.question_analysis",
                    value=analysis.model_dump(),
                    source=self.name,
                )
            ],
            derived_parameters={
                "open_variables": analysis.open_variables,
                "retrieval_queries": analysis.retrieval_queries,
                "hard_constraints": analysis.key_clinical_anchors,
            },
            context_updates=[
                f"Clinical question intent: {analysis.question_intent}",
                f"Open variables: {', '.join(analysis.open_variables) or 'none'}.",
            ],
        )


async def _analyze_with_agent(
    question: str,
    params: QueryParameters,
    deps,
) -> ClinicalQuestionAnalysisOutput | None:
    agent = getattr(deps, "clinical_analysis_agent", None)
    if agent is None:
        return None

    prompt = f"""
Question:
{question}

Return structured ClinicalQuestionAnalysisOutput only.
Set sufficient_context=false when critical patient-specific details are missing for safe guidance.
"""
    try:
        response = await agent.run(prompt, deps=deps)
        output = response.output
        if isinstance(output, ClinicalQuestionAnalysisOutput):
            if hasattr(deps, "log_agent_output"):
                deps.log_agent_output({"agent": "clinical_analysis", "success": True})
            return _normalize_analysis(output, question, params)
    except Exception as exc:  # noqa: BLE001
        if hasattr(deps, "log_agent_output"):
            deps.log_agent_output(
                {"agent": "clinical_analysis", "success": False, "error": repr(exc)},
            )
    return None


def _normalize_analysis(
    output: ClinicalQuestionAnalysisOutput,
    question: str,
    params: QueryParameters,
) -> ClinicalQuestionAnalysisOutput:
    queries = [q.strip() for q in output.retrieval_queries if q and q.strip()]
    if not queries:
        seed = params.refined_query_seed or strip_options(question)
        queries = [" ".join(seed.split())[:900]] if seed else []
    return output.model_copy(update={"retrieval_queries": queries[:3]})


def _minimal_fallback(question: str, refined_query_seed: str) -> ClinicalQuestionAnalysisOutput:
    seed = refined_query_seed or strip_options(question)
    seed = " ".join(seed.split())[:900]
    return ClinicalQuestionAnalysisOutput(
        question_intent="Provide evidence-based clinical guidance.",
        key_clinical_anchors=[],
        open_variables=[],
        retrieval_queries=[seed] if seed else [],
        sufficient_context=True,
        reasoning_instructions=["Use retrieved evidence as the primary source."],
    )
