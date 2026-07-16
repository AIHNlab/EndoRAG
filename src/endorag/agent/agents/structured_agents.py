from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from endorag.agent.agents.llm_client import get_model, ollama_structured_output
from endorag.providers.settings import get_settings
from endorag.retrieval.evidence_models import AnswerabilityAssessment
from endorag.agent.skills.clinical_question_models import ClinicalQuestionAnalysisOutput
from endorag.agent.skills.compose_guidance import GuidanceOutput
from endorag.agent.skills.mcq_stem_models import MCQStemAnalysisOutput
from endorag.agent.skills.reason_mcq_answer import (
    MCQArbiterOutput,
    MCQLetterOnlyOutput,
    MCQReasoningOutput,
    MCQVerificationOutput,
)


ANSWERABILITY_SYSTEM_PROMPT = """
You judge whether retrieved diabetes/endocrinology evidence is sufficient to answer the question.
Return a single JSON object with fields: sufficient (bool), confidence (low|medium|high),
rationale (string), missing_anchors (list of strings), supporting_quote (string or null),
closest_competing_options (list of strings), evidence_ids (list of strings).
Do not choose a final MCQ answer unless identifying close competitors.
If two or more answer options remain plausible, set sufficient=false, confidence=medium or low, and list
missing_anchors as short targeted search phrases that would distinguish the remaining options.
Populate closest_competing_options with the option letters/text still in contention.
Never invent guideline statements or citations.
"""


REASONING_SYSTEM_PROMPT = """
You answer endocrinology multiple-choice exam questions from retrieved evidence only.
You must always set selected_answer to exactly one lowercase letter (a, b, c, d, or e).
Use the decision context, answerability assessment, and retrieved passages together.
Evaluate every option before selecting one letter and follow STEM MODE / EXCEPT rules.
Weight passages with higher rerank scores. option_assessments.status must align with selected_answer.
Return a single JSON object with fields: selected_answer, confidence, option_assessments,
rationale, closest_runner_up, supporting_quote, limitations.
Never invent citations, thresholds, contraindications, or guideline statements.
"""


VERIFIER_SYSTEM_PROMPT = """
You verify an endocrinology MCQ answer against retrieved evidence.
Return a single JSON object with fields: verdict, selected_answer_supported, corrected_answer,
needs_followup, followup_queries, rationale, evidence_ids, contradictions.
Use verdict=supported only when the selected option is directly supported by the passages.
Use verdict=refuted when another option is better supported or the selected option conflicts with evidence.
Use verdict=insufficient when the evidence does not distinguish the selected answer from competitors.
If another option is clearly better supported, set corrected_answer to one lowercase letter.
If more evidence is needed, set needs_followup=true and provide short option-discriminative followup_queries.
Do not invent citations, thresholds, contraindications, or guideline statements.
"""


BASELINE_SELECTOR_SYSTEM_PROMPT = """
You answer endocrinology multiple-choice exam questions like a single-pass RAG baseline.
Use only the supplied top evidence passages. Pick exactly one lowercase letter (a, b, c, d, or e).
Keep the rationale short and do not produce option-by-option assessments.
Pay close attention to EXCEPT/NOT wording and to sequence words such as initial, next, after, before, and follow-up.
Never invent citations, thresholds, contraindications, or guideline statements.
"""


ARBITER_SYSTEM_PROMPT = """
You arbitrate between two candidate MCQ answers using retrieved evidence.
You must choose only one of the provided candidates: agentic or baseline_selector.
Do not invent a third answer. Prefer the candidate whose rationale is directly grounded in the evidence and the exact stem wording.
Pay close attention to EXCEPT/NOT wording and sequence words such as initial, next, after, before, and follow-up.
Return one JSON object with fields: selected_answer, decision_source, confidence, rationale, evidence_ids.
Never invent citations, thresholds, contraindications, or guideline statements.
"""


STEM_ANALYSIS_SYSTEM_PROMPT = """
You analyze multiple-choice exam stems for an endocrinology assistant.
Do not choose an answer letter. Do not invent clinical facts.
Return one JSON object with fields:
stem_type (next_step|management|except|diagnosis|knowledge|other),
polarity (standard|except), question_intent, key_clinical_anchors,
retrieval_queries (1-3 short search strings for follow-up retrieval only), reasoning_instructions.
Set polarity=except only when the question explicitly asks for false/NOT/EXCEPT/least likely.
Ignore incidental negation in the clinical vignette when polarity is standard.
"""


GUIDANCE_SYSTEM_PROMPT = """
Compose diabetes/endocrinology guidance from retrieved evidence.
Return a single JSON object with fields: answer_markdown, confidence, assumptions,
safety_notes, evidence_ids.
State uncertainty and missing patient-specific details. Do not invent thresholds or citations.
"""


class PydanticAgentAdapter:
    """Normalize PydanticAI result access for the existing skill interface."""

    def __init__(self, agent: Any) -> None:
        self.agent = agent

    async def run(self, prompt: str, deps=None, output_type=None):
        del output_type
        result = await self.agent.run(prompt, deps=deps)
        output = getattr(result, "output", None)
        if output is None:
            output = getattr(result, "data", None)
        return SimpleNamespace(output=output)


def build_structured_agent(
    output_model: type,
    system_prompt: str,
    *,
    model_name: str | None = None,
    base_url: str | None = None,
    seed: int = 42,
) -> PydanticAgentAdapter:
    try:
        from pydantic_ai import Agent
        from pydantic_ai.settings import ModelSettings
    except ImportError as exc:
        raise RuntimeError(
            "pydantic-ai is not installed. Install requirements.txt before running agentic_workflow."
        ) from exc

    from endorag.agent.agents.deps import EndoRAGDeps

    settings = get_settings()
    agent = Agent(
        get_model(
            model_name or settings.ollama_model,
            base_url,
        ),
        output_type=ollama_structured_output(output_model),
        deps_type=EndoRAGDeps,
        retries={"output": 2},
        model_settings=ModelSettings(temperature=0.0, seed=seed),
        system_prompt=system_prompt,
    )
    return PydanticAgentAdapter(agent)


CLINICAL_ANALYSIS_SYSTEM_PROMPT = """
You analyze non-MCQ clinical questions for an endocrinology assistant.
Do not invent clinical facts. Do not write the final answer.
Return one JSON object with fields:
question_intent, key_clinical_anchors, open_variables, retrieval_queries (1-3 short strings),
sufficient_context (bool), reasoning_instructions.
Set sufficient_context=false when missing details would make single-shot guidance unsafe.
"""


def build_stem_analysis_agent(
    model_name: str | None = None,
    base_url: str | None = None,
    seed: int = 42,
) -> PydanticAgentAdapter:
    return build_structured_agent(
        MCQStemAnalysisOutput,
        STEM_ANALYSIS_SYSTEM_PROMPT,
        model_name=model_name,
        base_url=base_url,
        seed=seed,
    )


def build_clinical_analysis_agent(
    model_name: str | None = None,
    base_url: str | None = None,
    seed: int = 42,
) -> PydanticAgentAdapter:
    return build_structured_agent(
        ClinicalQuestionAnalysisOutput,
        CLINICAL_ANALYSIS_SYSTEM_PROMPT,
        model_name=model_name,
        base_url=base_url,
        seed=seed,
    )


def build_answerability_agent(
    model_name: str | None = None,
    base_url: str | None = None,
    seed: int = 42,
) -> PydanticAgentAdapter:
    return build_structured_agent(
        AnswerabilityAssessment,
        ANSWERABILITY_SYSTEM_PROMPT,
        model_name=model_name,
        base_url=base_url,
        seed=seed,
    )


def build_reasoning_agent(
    model_name: str | None = None,
    base_url: str | None = None,
    seed: int = 42,
) -> PydanticAgentAdapter:
    return build_structured_agent(
        MCQReasoningOutput,
        REASONING_SYSTEM_PROMPT,
        model_name=model_name,
        base_url=base_url,
        seed=seed,
    )


def build_verifier_agent(
    model_name: str | None = None,
    base_url: str | None = None,
    seed: int = 42,
) -> PydanticAgentAdapter:
    return build_structured_agent(
        MCQVerificationOutput,
        VERIFIER_SYSTEM_PROMPT,
        model_name=model_name,
        base_url=base_url,
        seed=seed,
    )


def build_baseline_selector_agent(
    model_name: str | None = None,
    base_url: str | None = None,
    seed: int = 42,
) -> PydanticAgentAdapter:
    return build_structured_agent(
        MCQLetterOnlyOutput,
        BASELINE_SELECTOR_SYSTEM_PROMPT,
        model_name=model_name,
        base_url=base_url,
        seed=seed,
    )


def build_arbiter_agent(
    model_name: str | None = None,
    base_url: str | None = None,
    seed: int = 42,
) -> PydanticAgentAdapter:
    return build_structured_agent(
        MCQArbiterOutput,
        ARBITER_SYSTEM_PROMPT,
        model_name=model_name,
        base_url=base_url,
        seed=seed,
    )


def build_guidance_agent(
    model_name: str | None = None,
    base_url: str | None = None,
    seed: int = 42,
) -> PydanticAgentAdapter:
    return build_structured_agent(
        GuidanceOutput,
        GUIDANCE_SYSTEM_PROMPT,
        model_name=model_name,
        base_url=base_url,
        seed=seed,
    )
