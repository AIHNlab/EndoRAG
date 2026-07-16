from __future__ import annotations

from pydantic import BaseModel, Field

from endorag.agent.planning.parameters import QueryParameters, detect_question_polarity, strip_options
from endorag.agent.skills.base import SkillContext, SkillResult, StatePatch
from endorag.agent.skills.mcq_stem_models import MCQStemAnalysisOutput


class AnalyzeMCQStemInput(BaseModel):
    stem_type: str = "other"
    options: list[dict] = Field(default_factory=list)
    refined_query_seed: str = ""


class AnalyzeMCQStemSkill:
    name = "analyze_mcq_stem"
    description = "Analyze MCQ type, polarity, options, and retrieval focus via structured LLM output."
    input_model = AnalyzeMCQStemInput

    async def run(
        self,
        task_id: str,
        inputs: AnalyzeMCQStemInput,
        context: SkillContext,
        deps,
    ) -> SkillResult:
        question = context.run_metadata.get("question", "")
        params = QueryParameters.model_validate(context.query_parameters or {})
        option_labels = [f"{item.get('letter')}. {item.get('text')}" for item in inputs.options]

        analysis = await _analyze_with_agent(question, option_labels, params, deps)
        if analysis is None:
            analysis = _minimal_fallback(question, inputs.refined_query_seed, option_labels)

        return SkillResult(
            task_id=task_id,
            skill_name=self.name,
            status="success",
            summary=f"Analyzed MCQ stem as {analysis.stem_type} ({analysis.polarity}).",
            data={"stem_analysis": analysis.model_dump()},
            state_patches=[StatePatch(op="set", key="mcq.stem_analysis", value=analysis.model_dump(), source=self.name)],
            derived_parameters={
                "stem_type": analysis.stem_type,
                "polarity": analysis.polarity,
                "retrieval_queries": analysis.retrieval_queries,
                "hard_constraints": analysis.key_clinical_anchors,
                "mcq_options": inputs.options,
            },
            context_updates=[
                f"MCQ stem type: {analysis.stem_type} ({analysis.polarity}).",
                f"Question intent: {analysis.question_intent}",
                f"Retrieval focus: {', '.join(analysis.retrieval_queries[:3]) or 'general evidence'}.",
            ],
        )


async def _analyze_with_agent(
    question: str,
    option_labels: list[str],
    params: QueryParameters,
    deps,
) -> MCQStemAnalysisOutput | None:
    agent = getattr(deps, "stem_analysis_agent", None)
    if agent is None:
        return None

    options_block = "\n".join(option_labels)
    prompt = f"""
Question:
{question}

Parsed options:
{options_block}

Return structured MCQStemAnalysisOutput only.
"""
    try:
        response = await agent.run(prompt, deps=deps)
        output = response.output
        if isinstance(output, MCQStemAnalysisOutput):
            output = _normalize_analysis(output, question, params)
            if hasattr(deps, "log_agent_output"):
                deps.log_agent_output({"agent": "stem_analysis", "success": True})
            return output
    except Exception as exc:  # noqa: BLE001
        if hasattr(deps, "log_agent_output"):
            deps.log_agent_output(
                {"agent": "stem_analysis", "success": False, "error": repr(exc)},
            )
    return None


def _normalize_analysis(
    output: MCQStemAnalysisOutput,
    question: str,
    params: QueryParameters,
) -> MCQStemAnalysisOutput:
    queries = [q.strip() for q in output.retrieval_queries if q and q.strip()]
    if not queries:
        seed = params.refined_query_seed or strip_options(question)
        queries = [" ".join(seed.split())[:900]]
    detected_polarity = detect_question_polarity(question)
    updates = {"retrieval_queries": queries[:3]}
    if detected_polarity == "except" and output.polarity != "except":
        notes = list(output.reasoning_instructions)
        notes.append("Treat this as an EXCEPT/NOT stem based on explicit question wording.")
        updates.update(
            {
                "polarity": "except",
                "stem_type": "except",
                "reasoning_instructions": notes,
            }
        )
    elif output.polarity == "except" and output.stem_type != "except":
        updates["stem_type"] = "except"
    return output.model_copy(update=updates)


def _minimal_fallback(
    question: str,
    refined_query_seed: str,
    option_labels: list[str],
) -> MCQStemAnalysisOutput:
    seed = refined_query_seed or strip_options(question)
    seed = " ".join(seed.split())[:900]
    polarity = detect_question_polarity(question)
    return MCQStemAnalysisOutput(
        stem_type="except" if polarity == "except" else "other",
        polarity=polarity,
        question_intent="Answer the multiple-choice question from retrieved evidence.",
        key_clinical_anchors=[],
        retrieval_queries=[seed] if seed else [],
        reasoning_instructions=[
            "Use retrieved passages as primary evidence.",
            "Evaluate every option before selecting one letter.",
        ],
    )
