from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from endorag.retrieval.evidence_models import AnswerabilityAssessment
from endorag.retrieval.evidence_ranking import rank_passages_for_question
from endorag.retrieval.query_variants import build_followup_query_variants
from endorag.agent.planning.parameters import QueryParameters
from endorag.agent.skills.base import SkillContext, SkillResult, StatePatch


class OptionAssessment(BaseModel):
    model_config = ConfigDict(extra="ignore")

    letter: str
    text: str
    status: Literal["supported", "contradicted", "uncertain"]
    rationale: str
    evidence_ids: list[str] = Field(default_factory=list)


class MCQReasoningOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    selected_answer: str
    confidence: Literal["low", "medium", "high"]
    option_assessments: list[OptionAssessment]
    rationale: str
    closest_runner_up: str | None = None
    supporting_quote: str | None = None
    limitations: list[str] = Field(default_factory=list)


class MCQDecisionContext(BaseModel):
    model_config = ConfigDict(extra="ignore")

    reasoning_mode: Literal["anchor", "followup"]
    prompt_variant: str
    stem_type: str
    polarity: str
    question_intent: str = ""
    key_clinical_anchors: list[str] = Field(default_factory=list)
    reasoning_instructions: list[str] = Field(default_factory=list)
    answerability_sufficient: bool | None = None
    answerability_confidence: str | None = None
    answerability_rationale: str = ""
    missing_anchors: list[str] = Field(default_factory=list)
    closest_competing_options: list[str] = Field(default_factory=list)
    priority_evidence_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class MCQVerificationOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    verdict: Literal["supported", "refuted", "insufficient"]
    selected_answer_supported: bool
    corrected_answer: str | None = None
    needs_followup: bool = False
    followup_queries: list[str] = Field(default_factory=list)
    rationale: str
    evidence_ids: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)


class MCQLetterOnlyOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    selected_answer: str
    confidence: Literal["low", "medium", "high"]
    rationale: str
    evidence_ids: list[str] = Field(default_factory=list)


class MCQArbiterOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    selected_answer: str
    decision_source: Literal["agentic", "baseline_selector"]
    confidence: Literal["low", "medium", "high"]
    rationale: str
    evidence_ids: list[str] = Field(default_factory=list)


class ReasonMCQAnswerInput(BaseModel):
    dependency_results: dict = Field(default_factory=dict)
    evidence_pool: list[dict] = Field(default_factory=list)


class ReasonMCQAnswerSkill:
    name = "reason_mcq_answer"
    description = "Reason option by option and select a final MCQ answer letter."
    input_model = ReasonMCQAnswerInput

    async def run(
        self,
        task_id: str,
        inputs: ReasonMCQAnswerInput,
        context: SkillContext,
        deps,
    ) -> SkillResult:
        params = QueryParameters.model_validate(context.query_parameters or {})
        raw_passages = _collect_passages(inputs.dependency_results) or inputs.evidence_pool
        answerability = _collect_answerability(inputs.dependency_results)
        question = context.run_metadata.get("question", "")
        priority_ids = answerability.evidence_ids if answerability else None
        reasoning_mode = _reasoning_mode_from_context(context)
        passage_limit = 8 if reasoning_mode == "followup" else 5
        passages = rank_passages_for_question(
            raw_passages,
            anchor_query=question,
            priority_ids=priority_ids,
            limit=passage_limit,
        )
        anchor_passages = rank_passages_for_question(
            inputs.evidence_pool or raw_passages,
            anchor_query=question,
            priority_ids=priority_ids,
            limit=5,
        )

        if not params.options:
            return SkillResult(
                task_id=task_id,
                skill_name=self.name,
                status="invalid_question",
                summary="MCQ options are required.",
                limitations=["The input question does not include parseable MCQ answer options."],
            )

        agent = getattr(deps, "reasoning_agent", None)
        if agent is None:
            return SkillResult(
                task_id=task_id,
                skill_name=self.name,
                status="failed",
                summary="PydanticAI reasoning_agent is not configured.",
                limitations=["Configure deps.reasoning_agent before running MCQ evaluation."],
            )

        stem_analysis = _stem_analysis_from_context(context)
        if stem_analysis:
            params = params.model_copy(
                update={
                    "stem_type": stem_analysis.get("stem_type", params.stem_type),
                    "polarity": stem_analysis.get("polarity", params.polarity),
                }
            )

        decision_context = _build_decision_context(
            params,
            passages,
            answerability,
            stem_analysis=stem_analysis,
            reasoning_mode=reasoning_mode,
        )
        prompt = _build_reasoning_prompt(
            params,
            passages,
            decision_context,
            question=question,
        )
        used_fallback = False
        try:
            response = await agent.run(prompt, deps=deps)
            output = response.output
            if hasattr(deps, "log_agent_output"):
                deps.log_agent_output({"agent": "reasoning", "success": True})
        except Exception as exc:  # noqa: BLE001
            if hasattr(deps, "log_agent_output"):
                deps.log_agent_output(
                    {
                        "agent": "reasoning",
                        "success": True,
                        "fallback": "evidence_match",
                        "warning": repr(exc),
                    }
                )
            output = _fallback_reasoning(params, passages, answerability)
            output.limitations.append(f"PydanticAI reasoning_agent failed: {exc!r}")
            used_fallback = True

        reconciliation_notes: list[str] = []
        used_fallback = _normalize_and_reconcile_output(
            output,
            params,
            used_fallback=used_fallback,
            reconciliation_notes=reconciliation_notes,
        )

        verifier = await _verify_mcq_answer(
            output,
            params,
            passages,
            decision_context,
            question=question,
            deps=deps,
        )
        verifier_followup_queries: list[str] = []
        verifier_followup_used = False
        if verifier.needs_followup and getattr(deps, "vector_tools", None) is not None:
            followup_passages, verifier_followup_queries = await _retrieve_verifier_followup(
                params,
                verifier,
                decision_context,
                question=question,
                deps=deps,
            )
            if followup_passages:
                verifier_followup_used = True
                used_fallback = True
                reasoning_mode = "followup"
                raw_passages = _dedupe_dict_passages(passages + followup_passages)
                passages = rank_passages_for_question(
                    raw_passages,
                    anchor_query=question,
                    priority_ids=priority_ids,
                    limit=8,
                )
                decision_context = _build_decision_context(
                    params,
                    passages,
                    answerability,
                    stem_analysis=stem_analysis,
                    reasoning_mode=reasoning_mode,
                )
                prompt = _build_reasoning_prompt(
                    params,
                    passages,
                    decision_context,
                    question=question,
                )
                try:
                    response = await agent.run(prompt, deps=deps)
                    output = response.output
                    if hasattr(deps, "log_agent_output"):
                        deps.log_agent_output({"agent": "reasoning", "success": True, "variant": "verifier_followup"})
                    used_fallback = _normalize_and_reconcile_output(
                        output,
                        params,
                        used_fallback=used_fallback,
                        reconciliation_notes=reconciliation_notes,
                    )
                    verifier = await _verify_mcq_answer(
                        output,
                        params,
                        passages,
                        decision_context,
                        question=question,
                        deps=deps,
                    )
                except Exception as exc:  # noqa: BLE001
                    output.limitations.append(f"Verifier-triggered reasoning retry failed: {exc!r}")

        agentic_selected_answer = output.selected_answer
        baseline_candidate = await _select_baseline_answer(
            params,
            anchor_passages,
            decision_context,
            question=question,
            deps=deps,
        )
        baseline_selected_answer = normalize_mcq_letter(params, baseline_candidate.selected_answer)
        arbiter: MCQArbiterOutput | None = None
        arbiter_used = False
        final_decision_source = "agentic"
        if baseline_selected_answer and baseline_selected_answer == output.selected_answer:
            final_decision_source = "agreement"
        elif baseline_selected_answer:
            arbiter = await _arbitrate_mcq_answer(
                params,
                anchor_passages,
                decision_context,
                agentic_output=output,
                baseline_output=baseline_candidate,
                question=question,
                deps=deps,
            )
            arbiter_answer = normalize_mcq_letter(params, arbiter.selected_answer)
            if arbiter_answer:
                arbiter_used = True
                final_decision_source = f"arbiter:{arbiter.decision_source}"
                if arbiter_answer != output.selected_answer:
                    output.limitations.append(
                        f"Arbiter selected {arbiter_answer} from {arbiter.decision_source} candidate."
                    )
                    output.selected_answer = arbiter_answer
                    used_fallback = True

        limitations = list(output.limitations)
        if answerability and not answerability.sufficient:
            limitations.append("Answerability judge reported insufficient or incomplete evidence.")
        if verifier.verdict != "supported":
            limitations.append(f"Verifier verdict: {verifier.verdict}. {verifier.rationale}")
        status = "success"
        if used_fallback or not output.selected_answer:
            status = "partial"
        verifier_data = verifier.model_dump()
        verifier_data["followup_used"] = verifier_followup_used
        verifier_data["followup_queries"] = verifier_followup_queries or verifier.followup_queries
        arbiter_data = arbiter.model_dump() if arbiter else None
        return SkillResult(
            task_id=task_id,
            skill_name=self.name,
            status=status,
            summary=f"Selected answer {output.selected_answer} with {output.confidence} confidence.",
            data={
                "mcq_answer": output.model_dump(),
                "decision_context": decision_context.model_dump(),
                "reasoning_mode": reasoning_mode,
                "prompt_variant": decision_context.prompt_variant,
                "reconciliation_notes": reconciliation_notes,
                "verifier": verifier_data,
                "agentic_selected_answer": agentic_selected_answer,
                "baseline_selector": baseline_candidate.model_dump(),
                "baseline_selector_answer": baseline_selected_answer,
                "arbiter": arbiter_data,
                "arbiter_answer": (arbiter_data or {}).get("selected_answer") if arbiter_data else None,
                "arbiter_used": arbiter_used,
                "final_decision_source": final_decision_source,
                "selected_evidence_ids": _selected_evidence_ids(output),
            },
            evidence=passages,
            limitations=limitations,
            state_patches=[
                StatePatch(op="set", key="mcq.final_answer", value=output.model_dump(), source=self.name),
                StatePatch(op="set", key="mcq.decision_context", value=decision_context.model_dump(), source=self.name),
                StatePatch(op="set", key="mcq.verifier", value=verifier_data, source=self.name),
                StatePatch(
                    op="set",
                    key="mcq.decision_trace",
                    value={
                        "agentic_selected_answer": agentic_selected_answer,
                        "baseline_selector_answer": baseline_selected_answer,
                        "arbiter_answer": (arbiter_data or {}).get("selected_answer") if arbiter_data else None,
                        "arbiter_used": arbiter_used,
                        "final_decision_source": final_decision_source,
                    },
                    source=self.name,
                ),
            ],
            context_updates=[
                f"Final MCQ answer: {output.selected_answer}.",
                f"Closest runner-up: {output.closest_runner_up or 'not specified'}.",
                f"Verifier verdict: {verifier.verdict}.",
                f"Decision source: {final_decision_source}.",
            ],
        )


def _collect_passages(dependency_results: dict) -> list[dict]:
    passages: list[dict] = []
    for result in dependency_results.values():
        if not isinstance(result, dict):
            continue
        passages.extend(result.get("evidence") or [])
        passages.extend((result.get("data") or {}).get("passages") or [])
    deduped: list[dict] = []
    seen: set[object] = set()
    for passage in passages:
        if not isinstance(passage, dict):
            continue
        key = passage.get("id") or (passage.get("source"), str(passage.get("text", ""))[:500])
        if key not in seen:
            seen.add(key)
            deduped.append(passage)
    return deduped


def _dedupe_dict_passages(passages: list[dict]) -> list[dict]:
    deduped: list[dict] = []
    seen: set[object] = set()
    for passage in passages:
        if not isinstance(passage, dict):
            continue
        key = passage.get("id") or (passage.get("source"), str(passage.get("text", ""))[:500])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(passage)
    return deduped


def _collect_answerability(dependency_results: dict) -> AnswerabilityAssessment | None:
    for result in dependency_results.values():
        if not isinstance(result, dict):
            continue
        data = result.get("data") or {}
        if "answerability" in data:
            return AnswerabilityAssessment.model_validate(data["answerability"])
    return None


def _stem_analysis_from_context(context: SkillContext) -> dict:
    shared = context.shared_state or {}
    analysis = shared.get("mcq", {}).get("stem_analysis")
    return analysis if isinstance(analysis, dict) else {}


def _reasoning_mode_from_context(context: SkillContext) -> str:
    mode = (context.shared_state or {}).get("reasoning_mode")
    return mode if mode in {"anchor", "followup"} else "anchor"


def _build_decision_context(
    params: QueryParameters,
    passages: list[dict],
    answerability: AnswerabilityAssessment | None,
    *,
    stem_analysis: dict | None = None,
    reasoning_mode: str = "anchor",
) -> MCQDecisionContext:
    stem = stem_analysis or {}
    evidence_ids = [str(passage.get("id")) for passage in passages if passage.get("id")]
    return MCQDecisionContext(
        reasoning_mode=reasoning_mode if reasoning_mode in {"anchor", "followup"} else "anchor",
        prompt_variant="mcq_decision_context_v1",
        stem_type=params.stem_type,
        polarity=params.polarity,
        question_intent=str(stem.get("question_intent") or ""),
        key_clinical_anchors=[
            str(item).strip()[:240]
            for item in (stem.get("key_clinical_anchors") or params.hard_constraints or [])
            if str(item).strip()
        ][:6],
        reasoning_instructions=[
            str(item).strip()[:240]
            for item in (stem.get("reasoning_instructions") or [])
            if str(item).strip()
        ][:5],
        answerability_sufficient=answerability.sufficient if answerability else None,
        answerability_confidence=answerability.confidence if answerability else None,
        answerability_rationale=(answerability.rationale if answerability else "")[:800],
        missing_anchors=[
            str(item).strip()[:240]
            for item in (answerability.missing_anchors if answerability else [])
            if str(item).strip()
        ][:6],
        closest_competing_options=[
            str(item).strip()[:240]
            for item in (answerability.closest_competing_options if answerability else [])
            if str(item).strip()
        ][:5],
        priority_evidence_ids=[str(item) for item in (answerability.evidence_ids if answerability else [])],
        evidence_ids=evidence_ids[:8],
    )


def _build_reasoning_prompt(
    params: QueryParameters,
    passages: list[dict],
    decision_context: MCQDecisionContext,
    *,
    question: str = "",
) -> str:
    options = "\n".join(f"{option.letter}. {option.text}" for option in params.options)
    evidence_limit = 8 if decision_context.reasoning_mode == "followup" else 5
    evidence = "\n\n".join(
        f"[{p.get('id')}] score={p.get('score')} Source: {p.get('source')}\n{p.get('text')}"
        for p in passages[:evidence_limit]
    )
    question_line = question.strip()
    if len(question_line) > 3500:
        question_line = question_line[:3500]

    is_except = params.polarity == "except"
    stem_mode = _stem_mode_instructions(is_except)
    reasoning_block = "\n".join(f"- {item}" for item in decision_context.reasoning_instructions)
    return f"""
{stem_mode}

Decision context:
{decision_context.model_dump()}

MCQ question:
{question_line}

Options:
{options}

Stem reasoning notes:
{reasoning_block or "- Evaluate each option against the evidence."}

Evidence (highest relevance first):
{evidence}

Return structured MCQReasoningOutput only.
- Assess every option (a–e) before selecting one letter.
- selected_answer must be one lowercase letter a-e.
- Each option_assessments[].status must be consistent with selected_answer.
- For STANDARD stems: selected_answer must be an option with status "supported".
- For EXCEPT stems: selected_answer must be the exception (usually status "contradicted").
- If answerability says evidence is insufficient or options are competing, be conservative and reflect that in confidence/limitations.
"""


def _stem_mode_instructions(is_except: bool) -> str:
    if is_except:
        return (
            "STEM MODE: EXCEPT/NOT. The question asks for the outlier or option that does NOT apply. "
            "Select the exception; mark it supported or contradicted accordingly and do not pick the "
            "best routine management option unless it is the exception."
        )
    return (
        "STEM MODE: STANDARD. The question asks for the best, most likely, or most appropriate option. "
        "Do NOT use EXCEPT/NOT logic. Ignore incidental words like 'not' in the clinical vignette."
    )


def _normalize_and_reconcile_output(
    output: MCQReasoningOutput,
    params: QueryParameters,
    *,
    used_fallback: bool,
    reconciliation_notes: list[str],
) -> bool:
    normalized = normalize_mcq_letter(params, output.selected_answer)
    if normalized:
        output.selected_answer = normalized
    elif not output.selected_answer:
        output.limitations.append("Model did not return a valid MCQ letter.")

    reconciled, notes = reconcile_selected_answer(
        output,
        params,
        is_except=params.polarity == "except",
    )
    if reconciled and reconciled != output.selected_answer:
        output.selected_answer = reconciled
        used_fallback = True
    output.limitations.extend(notes)
    reconciliation_notes.extend(notes)
    return used_fallback


async def _select_baseline_answer(
    params: QueryParameters,
    passages: list[dict],
    decision_context: MCQDecisionContext,
    *,
    question: str,
    deps,
) -> MCQLetterOnlyOutput:
    agent = getattr(deps, "baseline_selector_agent", None)
    if agent is None:
        return _fallback_baseline_selector(params, passages)

    prompt = _build_baseline_selector_prompt(
        params,
        passages,
        decision_context,
        question=question,
    )
    try:
        response = await agent.run(prompt, deps=deps)
        output = response.output
        selected = normalize_mcq_letter(params, output.selected_answer)
        if hasattr(deps, "log_agent_output"):
            deps.log_agent_output({"agent": "baseline_selector", "success": True})
        return output.model_copy(update={"selected_answer": selected or output.selected_answer})
    except Exception as exc:  # noqa: BLE001
        if hasattr(deps, "log_agent_output"):
            deps.log_agent_output(
                {
                    "agent": "baseline_selector",
                    "success": False,
                    "recoverable": True,
                    "fallback": "lexical_evidence_match",
                    "error": repr(exc),
                }
            )
        fallback = _fallback_baseline_selector(params, passages)
        fallback.rationale = f"Baseline selector fallback used after agent failure: {exc!r}"
        return fallback


def _build_baseline_selector_prompt(
    params: QueryParameters,
    passages: list[dict],
    decision_context: MCQDecisionContext,
    *,
    question: str,
) -> str:
    options = "\n".join(f"{option.letter}. {option.text}" for option in params.options)
    evidence = "\n\n".join(
        f"[{p.get('id')}] score={p.get('score')} Source: {p.get('source')}\n{p.get('text')}"
        for p in passages[:5]
    )
    return f"""
Answer this MCQ from the evidence only, like a single-pass RAG baseline.

Stem mode: {params.polarity}
Question intent: {decision_context.question_intent or "not specified"}

Question:
{question}

Options:
{options}

Evidence (top 5):
{evidence}

Return MCQLetterOnlyOutput only.
- selected_answer must be one lowercase letter a-e.
- For EXCEPT/NOT stems, choose the outlier.
- For investigation/management stems, respect the exact sequence requested by the question.
- If evidence says the ideal answer is not listed, choose the closest listed exam option and explain briefly.
"""


def _fallback_baseline_selector(
    params: QueryParameters,
    passages: list[dict],
) -> MCQLetterOnlyOutput:
    selected = best_effort_mcq_letter(params, passages) or (
        params.options[0].letter if params.options else ""
    )
    return MCQLetterOnlyOutput(
        selected_answer=selected,
        confidence="low",
        rationale="Fallback selected the option with the strongest lexical overlap in anchor evidence.",
        evidence_ids=[str(passage.get("id")) for passage in passages[:2] if passage.get("id")],
    )


async def _arbitrate_mcq_answer(
    params: QueryParameters,
    passages: list[dict],
    decision_context: MCQDecisionContext,
    *,
    agentic_output: MCQReasoningOutput,
    baseline_output: MCQLetterOnlyOutput,
    question: str,
    deps,
) -> MCQArbiterOutput:
    agent = getattr(deps, "arbiter_agent", None)
    if agent is None:
        return _fallback_arbiter(params, agentic_output, baseline_output)

    prompt = _build_arbiter_prompt(
        params,
        passages,
        decision_context,
        agentic_output=agentic_output,
        baseline_output=baseline_output,
        question=question,
    )
    try:
        response = await agent.run(prompt, deps=deps)
        output = response.output
        selected = normalize_mcq_letter(params, output.selected_answer)
        agentic = normalize_mcq_letter(params, agentic_output.selected_answer)
        baseline = normalize_mcq_letter(params, baseline_output.selected_answer)
        if selected not in {agentic, baseline}:
            output = _fallback_arbiter(params, agentic_output, baseline_output)
        else:
            output = output.model_copy(update={"selected_answer": selected})
        if hasattr(deps, "log_agent_output"):
            deps.log_agent_output({"agent": "arbiter", "success": True})
        return output
    except Exception as exc:  # noqa: BLE001
        if hasattr(deps, "log_agent_output"):
            deps.log_agent_output({"agent": "arbiter", "success": False, "error": repr(exc)})
        output = _fallback_arbiter(params, agentic_output, baseline_output)
        output.rationale = f"Arbiter fallback used after agent failure: {exc!r}"
        return output


def _build_arbiter_prompt(
    params: QueryParameters,
    passages: list[dict],
    decision_context: MCQDecisionContext,
    *,
    agentic_output: MCQReasoningOutput,
    baseline_output: MCQLetterOnlyOutput,
    question: str,
) -> str:
    options = "\n".join(f"{option.letter}. {option.text}" for option in params.options)
    evidence = "\n\n".join(
        f"[{p.get('id')}] score={p.get('score')} Source: {p.get('source')}\n{p.get('text')}"
        for p in passages[:5]
    )
    agentic_candidate = normalize_mcq_letter(params, agentic_output.selected_answer)
    baseline_candidate = normalize_mcq_letter(params, baseline_output.selected_answer)
    return f"""
Choose between exactly two MCQ answer candidates. Do not choose any other letter.

Question:
{question}

Options:
{options}

Decision context:
{decision_context.model_dump()}

Candidate agentic:
letter={agentic_candidate}
rationale={agentic_output.rationale}
option_assessments={ [assessment.model_dump() for assessment in agentic_output.option_assessments] }

Candidate baseline_selector:
letter={baseline_candidate}
rationale={baseline_output.rationale}

Evidence (anchor top 5):
{evidence}

Return MCQArbiterOutput only.
- selected_answer must be either {agentic_candidate} or {baseline_candidate}.
- decision_source must be either "agentic" or "baseline_selector".
- Prefer direct evidence and exact stem wording over broad clinical pattern matching.
- For EXCEPT/NOT stems, choose the candidate that best identifies the outlier.
- For investigation/management stems, choose the candidate that best respects sequence words such as initial, next, after, before, and follow-up.
"""


def _fallback_arbiter(
    params: QueryParameters,
    agentic_output: MCQReasoningOutput,
    baseline_output: MCQLetterOnlyOutput,
) -> MCQArbiterOutput:
    agentic = normalize_mcq_letter(params, agentic_output.selected_answer)
    baseline = normalize_mcq_letter(params, baseline_output.selected_answer)
    if agentic_output.confidence == "low" and baseline:
        selected = baseline
        source = "baseline_selector"
    else:
        selected = agentic or baseline
        source = "agentic" if agentic else "baseline_selector"
    return MCQArbiterOutput(
        selected_answer=selected,
        decision_source=source,
        confidence="low",
        rationale="Fallback arbiter used confidence and candidate validity only.",
        evidence_ids=[],
    )


async def _verify_mcq_answer(
    output: MCQReasoningOutput,
    params: QueryParameters,
    passages: list[dict],
    decision_context: MCQDecisionContext,
    *,
    question: str,
    deps,
) -> MCQVerificationOutput:
    agent = getattr(deps, "verifier_agent", None)
    if agent is None:
        return _deterministic_verification(output, params, decision_context)

    prompt = _build_verification_prompt(
        output,
        params,
        passages,
        decision_context,
        question=question,
    )
    try:
        response = await agent.run(prompt, deps=deps)
        verifier = response.output
        if hasattr(deps, "log_agent_output"):
            deps.log_agent_output({"agent": "verifier", "success": True})
        return _normalize_verification(verifier, params)
    except Exception as exc:  # noqa: BLE001
        if hasattr(deps, "log_agent_output"):
            deps.log_agent_output({"agent": "verifier", "success": False, "error": repr(exc)})
        verifier = _deterministic_verification(output, params, decision_context)
        verifier.contradictions.append(f"Verifier agent failed: {exc!r}")
        return verifier


def _build_verification_prompt(
    output: MCQReasoningOutput,
    params: QueryParameters,
    passages: list[dict],
    decision_context: MCQDecisionContext,
    *,
    question: str,
) -> str:
    options = "\n".join(f"{option.letter}. {option.text}" for option in params.options)
    evidence = "\n\n".join(
        f"[{p.get('id')}] score={p.get('score')} Source: {p.get('source')}\n{p.get('text')}"
        for p in passages[:8]
    )
    return f"""
Question:
{question}

Options:
{options}

Decision context:
{decision_context.model_dump()}

Candidate reasoning output:
{output.model_dump()}

Evidence:
{evidence}

Verify whether selected_answer is supported by the evidence and decision context.
Return MCQVerificationOutput only.
"""


def _normalize_verification(
    verifier: MCQVerificationOutput,
    params: QueryParameters,
) -> MCQVerificationOutput:
    corrected = normalize_mcq_letter(params, verifier.corrected_answer or "")
    queries = [
        " ".join(str(query).split())[:240]
        for query in verifier.followup_queries
        if str(query).strip()
    ][:4]
    return verifier.model_copy(
        update={
            "corrected_answer": corrected or None,
            "followup_queries": queries,
            "evidence_ids": [str(item) for item in verifier.evidence_ids],
        }
    )


def _deterministic_verification(
    output: MCQReasoningOutput,
    params: QueryParameters,
    decision_context: MCQDecisionContext,
) -> MCQVerificationOutput:
    selected = normalize_mcq_letter(params, output.selected_answer)
    by_letter = {
        normalize_mcq_letter(params, assessment.letter): assessment
        for assessment in output.option_assessments
    }
    selected_assessment = by_letter.get(selected)
    supported = selected_assessment is not None and selected_assessment.status == "supported"
    contradicted = selected_assessment is not None and selected_assessment.status == "contradicted"
    if params.polarity == "except":
        supported = selected_assessment is not None and selected_assessment.status in {"contradicted", "uncertain"}
        contradicted = selected_assessment is not None and selected_assessment.status == "supported"

    corrected = None
    if contradicted:
        candidate_status = "contradicted" if params.polarity == "except" else "supported"
        candidates = [
            letter
            for letter, assessment in by_letter.items()
            if letter and assessment.status == candidate_status
        ]
        if len(candidates) == 1:
            corrected = candidates[0]

    needs_followup = (
        not supported
        and bool(decision_context.missing_anchors or decision_context.closest_competing_options)
    )
    if decision_context.answerability_sufficient is False and not supported:
        needs_followup = True

    if supported:
        verdict = "supported"
        rationale = "Selected answer is consistent with option assessments."
    elif contradicted:
        verdict = "refuted"
        rationale = "Selected answer conflicts with option assessments."
    else:
        verdict = "insufficient"
        rationale = "Option assessments do not provide clear support for the selected answer."

    return MCQVerificationOutput(
        verdict=verdict,
        selected_answer_supported=supported,
        corrected_answer=corrected,
        needs_followup=needs_followup,
        followup_queries=[],
        rationale=rationale,
        evidence_ids=_selected_evidence_ids(output),
        contradictions=[] if supported else [rationale],
    )


async def _retrieve_verifier_followup(
    params: QueryParameters,
    verifier: MCQVerificationOutput,
    decision_context: MCQDecisionContext,
    *,
    question: str,
    deps,
) -> tuple[list[dict], list[str]]:
    queries = build_followup_query_variants(
        params,
        missing_anchors=decision_context.missing_anchors or verifier.followup_queries,
        supplemental_queries=verifier.followup_queries,
        competing_options=decision_context.closest_competing_options,
    )
    if not queries:
        return [], []

    retrieved: list[dict] = []
    attempted: list[str] = []
    for query in queries[:4]:
        passages, call_provenance = await deps.vector_tools.retrieve(
            query=query,
            domain=getattr(deps, "routing_domain", None) or params.suspected_domain,
            top_k=None,
            retrieval_round=2,
        )
        attempted.append(query)
        if hasattr(deps, "log_tool_call"):
            provenance = dict(call_provenance)
            provenance["trigger"] = "verifier_followup"
            deps.log_tool_call(provenance)
        retrieved.extend(passage.model_dump() for passage in passages)

    return _dedupe_dict_passages(retrieved), attempted


def _selected_evidence_ids(output: MCQReasoningOutput) -> list[str]:
    selected = str(output.selected_answer or "").lower()
    for assessment in output.option_assessments:
        if str(assessment.letter).lower() == selected:
            return [str(item) for item in assessment.evidence_ids]
    return []


def reconcile_selected_answer(
    output: MCQReasoningOutput,
    params: QueryParameters,
    *,
    is_except: bool,
) -> tuple[str, list[str]]:
    """Align selected_answer with option_assessments when the model disagrees with itself."""
    notes: list[str] = []
    declared = normalize_mcq_letter(params, output.selected_answer)

    by_letter: dict[str, OptionAssessment] = {}
    for assessment in output.option_assessments:
        letter = normalize_mcq_letter(params, assessment.letter)
        if letter:
            by_letter[letter] = assessment

    if not by_letter:
        return declared, notes

    if is_except:
        contradicted = [letter for letter, oa in by_letter.items() if oa.status == "contradicted"]
        supported = [letter for letter, oa in by_letter.items() if oa.status == "supported"]
        if declared in contradicted:
            pick = declared
        elif declared in supported and len(contradicted) == 1:
            pick = contradicted[0]
        else:
            pick = declared
    else:
        supported = [letter for letter, oa in by_letter.items() if oa.status == "supported"]
        contradicted = {letter for letter, oa in by_letter.items() if oa.status == "contradicted"}

        if declared in contradicted and len(supported) == 1:
            pick = supported[0]
            notes.append(
                f"Reconciled selected_answer from {declared} to {pick}: "
                "declared option was contradicted and exactly one option was supported."
            )
            return pick, notes
        else:
            pick = declared

    if pick and declared and pick != declared:
        if not any(note.startswith("Reconciled selected_answer") for note in notes):
            notes.append(
                f"Reconciled selected_answer from {declared} to {pick} based on option_assessments."
            )
    return pick or declared, notes


def normalize_mcq_letter(params: QueryParameters, raw: str) -> str:
    """Map model output to a single option letter when possible."""
    text = str(raw or "").strip().lower()
    if text in {"a", "b", "c", "d", "e"}:
        return text
    match = re.search(r"\boption\s*([a-e])\b", text)
    if match:
        return match.group(1)
    match = re.search(r"\b([a-e])\s*[\).:]", text)
    if match:
        return match.group(1)
    compact = re.sub(r"\s+", " ", text)
    options = sorted(params.options, key=lambda option: len(option.text), reverse=True)
    for option in options:
        option_text = re.sub(r"\s+", " ", option.text.lower())
        if not compact:
            continue
        if compact == option_text:
            return option.letter
        if re.search(rf"\b{re.escape(option_text)}\b", compact):
            return option.letter
        if re.search(rf"\b{re.escape(compact)}\b", option_text):
            return option.letter
    return ""


def best_effort_mcq_letter(params: QueryParameters, passages: list[dict]) -> str:
    """Pick the option best supported by retrieved passage text."""
    if not params.options:
        return ""
    corpus = " ".join(str(passage.get("text", "")) for passage in passages).lower()
    if not corpus.strip():
        return ""

    best_letter = ""
    best_score = 0
    for option in params.options:
        score = _option_evidence_score(option.text, corpus)
        if score > best_score:
            best_score = score
            best_letter = option.letter
    return best_letter if best_score > 0 else ""


def _option_evidence_score(option_text: str, corpus: str) -> int:
    score = 0
    option_lower = option_text.lower()
    if option_lower and option_lower in corpus:
        score += 8
    numbers = re.findall(r"\d+(?:\.\d+)?", option_lower)
    for number in numbers:
        if number in corpus:
            score += 4
    tokens = [token for token in re.findall(r"[a-z0-9]+", option_lower) if len(token) > 2]
    for token in tokens:
        if token in corpus:
            score += 1
    return score


def _fallback_reasoning(
    params: QueryParameters,
    passages: list[dict],
    answerability: AnswerabilityAssessment | None,
) -> MCQReasoningOutput:
    quote = answerability.supporting_quote if answerability else None
    if not quote and passages:
        quote = str(passages[0].get("text", ""))[:250]
    selected = best_effort_mcq_letter(params, passages) or (
        params.options[0].letter if params.options else ""
    )
    return MCQReasoningOutput(
        selected_answer=selected,
        confidence="low",
        option_assessments=[
            OptionAssessment(
                letter=option.letter,
                text=option.text,
                status="uncertain",
                rationale="Fallback reasoning could not run an LLM assessment.",
                evidence_ids=[],
            )
            for option in params.options
        ],
        rationale=(
            "Fallback selected the best-supported option from retrieved evidence because "
            "structured reasoning was unavailable."
        ),
        closest_runner_up=params.options[1].letter if len(params.options) > 1 else None,
        supporting_quote=quote,
        limitations=["Reasoning agent was not configured or structured output failed."],
    )
