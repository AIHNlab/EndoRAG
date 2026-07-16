from __future__ import annotations

from endorag.agent.agents.deps import EndoRAGDeps
from endorag.agent.execution.plan_executor import execute_plan
from endorag.agent.orchestration.common_context import (
    append_common_context,
    append_result_context,
    render_common_context,
)
from endorag.agent.planning.manager import create_task_plan
from endorag.agent.planning.models import PlannedTask, TaskPlan
from endorag.agent.planning.parameters import (
    QueryParameters,
    query_parameters_from_standardized,
)
from endorag.retrieval.evidence_ranking import rank_passages_for_question
from endorag.agent.planning.question_normalize import standardize_exam_question
from endorag.agent.planning.validator import validate_plan
from endorag.agent.response.composer import compose_response
from endorag.agent.skills.base import SkillContext


async def understand_question_node(state: dict) -> dict:
    raw_question = state.get("original_question") or state.get("question", "")
    standardized = standardize_exam_question(raw_question)
    question = standardized.canonical
    query_parameters = query_parameters_from_standardized(standardized)
    common_context_text = render_common_context(state.get("common_context", []))
    context = SkillContext(
        run_metadata=state.get("run_metadata", {}),
        query_parameters=query_parameters.model_dump(),
        shared_state=state.get("shared_execution_state", {}),
        working_context=query_parameters.working_context_seed,
    )
    context.run_metadata["question"] = question
    context.run_metadata["original_question"] = standardized.original
    context.run_metadata["input_format"] = standardized.format
    deps = _build_deps(state)
    plan = await create_task_plan(
        "understand_question",
        question,
        context,
        deps,
        query_parameters=query_parameters,
        common_context=common_context_text,
    )
    errors, warnings = validate_plan(
        plan,
        context,
        question=question,
        query_parameters=query_parameters,
        stage="understand_question",
    )
    if errors:
        return {**state, "validation_errors": errors, "validation_warnings": warnings}

    results = await execute_plan(plan, context, deps)
    common_context = state.get("common_context", [])
    for result in results:
        common_context = append_result_context(common_context, result, kind="question")

    return _with_dep_logs(
        deps,
        {
            **state,
            "question": question,
            "original_question": standardized.original,
            "query_parameters": context.query_parameters,
            "run_metadata": context.run_metadata,
            "shared_execution_state": context.shared_state,
            "working_context": context.working_context,
            "common_context": common_context,
            "stage_plans": {**state.get("stage_plans", {}), "understand_question": plan.model_dump()},
            "stage_outputs": {
                **state.get("stage_outputs", {}),
                "understand_question": {"results": [result.model_dump() for result in results]},
            },
            "validation_errors": errors,
            "validation_warnings": warnings,
        },
    )


async def retrieve_and_validate_evidence_node(state: dict) -> dict:
    query_parameters = QueryParameters.model_validate(state.get("query_parameters", {}))
    context = SkillContext(
        run_metadata=state.get("run_metadata", {}),
        query_parameters=query_parameters.model_dump(),
        shared_state=state.get("shared_execution_state", {}),
        working_context=render_common_context(state.get("common_context", [])),
    )
    deps = _build_deps(state)
    plan = _build_retrieval_stage_plan(query_parameters)
    errors, warnings = validate_plan(
        plan,
        context,
        question=state["question"],
        query_parameters=query_parameters,
        stage="retrieve_and_validate_evidence",
    )
    if errors:
        return {**state, "validation_errors": errors, "validation_warnings": warnings}

    results = await execute_plan(plan, context, deps)
    evidence_pool = _merge_evidence_pool(
        state.get("evidence_pool", []),
        results,
        anchor_query=state.get("question", ""),
        limit=_anchor_evidence_limit(),
    )
    context.shared_state.setdefault("retrieval", {})["accumulated_passages"] = evidence_pool
    common_context = _append_retrieval_common_context(state.get("common_context", []), results)

    followup_results = []
    answerability = _latest_answerability(results)
    max_followups = max(0, int(getattr(getattr(deps, "settings", None), "max_retrieval_rounds", 2)) - 1)
    followup_round = 0
    context.shared_state["reasoning_mode"] = "anchor"
    while _should_run_followup(answerability) and followup_round < max_followups:
        followup_round += 1
        context.shared_state["reasoning_mode"] = "followup"
        trigger_reason = _followup_trigger_reason(answerability)
        context.shared_state.setdefault("retrieval", {}).setdefault("followup_triggers", []).append(
            {"round": followup_round, "reason": trigger_reason}
        )
        followup_plan = _build_followup_retrieval_plan(answerability, query_parameters)
        followup_errors, followup_warnings = validate_plan(
            followup_plan,
            context,
            question=state["question"],
            query_parameters=query_parameters,
            stage="retrieve_and_validate_evidence",
        )
        warnings.extend(followup_warnings)
        if not followup_errors:
            round_results = await execute_plan(followup_plan, context, deps)
            followup_results.extend(round_results)
            evidence_pool = _merge_evidence_pool(
                evidence_pool,
                round_results,
                anchor_query=state.get("question", ""),
                limit=_followup_evidence_limit(),
            )
            context.shared_state.setdefault("retrieval", {})["accumulated_passages"] = evidence_pool
            common_context = _append_retrieval_common_context(common_context, round_results)
            answerability = _latest_answerability(round_results) or answerability
        else:
            warnings.extend(followup_errors)
            break

    all_results = results + followup_results
    return _with_dep_logs(
        deps,
        {
            **state,
            "run_metadata": context.run_metadata,
            "shared_execution_state": context.shared_state,
            "working_context": context.working_context,
            "common_context": common_context,
            "evidence_pool": evidence_pool,
            "stage_plans": {**state.get("stage_plans", {}), "retrieve_and_validate_evidence": plan.model_dump()},
            "stage_outputs": {
                **state.get("stage_outputs", {}),
                "retrieve_and_validate_evidence": {"results": [result.model_dump() for result in all_results]},
            },
            "validation_errors": errors,
            "validation_warnings": warnings,
        },
    )


async def reason_and_compose_answer_node(state: dict) -> dict:
    query_parameters = QueryParameters.model_validate(state.get("query_parameters", {}))
    context = SkillContext(
        run_metadata=state.get("run_metadata", {}),
        query_parameters=query_parameters.model_dump(),
        shared_state=state.get("shared_execution_state", {}),
        working_context=render_common_context(state.get("common_context", [])),
    )
    deps = _build_deps(state)
    plan = _build_reasoning_stage_plan(query_parameters, state)
    errors, warnings = validate_plan(
        plan,
        context,
        question=state["question"],
        query_parameters=query_parameters,
        stage="reason_and_compose_answer",
    )
    if errors:
        return {**state, "validation_errors": errors, "validation_warnings": warnings}

    results = await execute_plan(plan, context, deps)
    common_context = state.get("common_context", [])
    for result in results:
        common_context = append_result_context(common_context, result, kind="reasoning")

    response = await compose_response(
        question=state["question"],
        plan=plan,
        results=results,
        deps=deps,
        working_context=render_common_context(common_context),
        shared_execution_state={
            **state.get("shared_execution_state", {}),
            "evidence_pool": state.get("evidence_pool", []),
            "stage_outputs": state.get("stage_outputs", {}),
        },
    )
    common_context = append_common_context(
        common_context,
        source="reason_and_compose_answer",
        kind="reasoning",
        summaries=[f"Final response confidence={response.confidence}.", response.answer_markdown[:280]],
        importance="high",
    )
    return _with_dep_logs(
        deps,
        {
            **state,
            "run_metadata": context.run_metadata,
            "shared_execution_state": context.shared_state,
            "working_context": context.working_context,
            "common_context": common_context,
            "stage_plans": {**state.get("stage_plans", {}), "reason_and_compose_answer": plan.model_dump()},
            "stage_outputs": {
                **state.get("stage_outputs", {}),
                "reason_and_compose_answer": {"results": [result.model_dump() for result in results]},
            },
            "validation_errors": errors,
            "validation_warnings": warnings,
            "final_response": response.model_dump(),
            "final_answer": response.answer_markdown,
        },
    )


async def handle_error_node(state: dict) -> dict:
    errors = state.get("validation_errors") or [state.get("error") or "Unknown workflow error."]
    answer = "Workflow validation failed: " + "; ".join(str(error) for error in errors)
    return {**state, "final_answer": answer, "error": answer}


def should_continue_after_question_understanding(state: dict) -> str:
    if state.get("validation_errors"):
        return "error"
    results = (state.get("stage_outputs", {}).get("understand_question", {}) or {}).get("results", [])
    if any(result.get("status") in {"invalid_question", "insufficient_context"} for result in results):
        return "end"
    return "retrieve"


def should_continue_after_retrieval(state: dict) -> str:
    if state.get("validation_errors"):
        return "error"
    return "reason"


def _build_deps(state: dict) -> EndoRAGDeps:
    deps = state.get("_deps")
    if isinstance(deps, EndoRAGDeps):
        return deps
    return EndoRAGDeps(vector_tools=state.get("vector_tools"))


def _with_dep_logs(deps: EndoRAGDeps, state: dict) -> dict:
    return {
        **state,
        "task_executions": deps.task_executions,
        "tool_calls": deps.tool_calls,
        "agent_outputs": deps.agent_outputs,
        "runtime_events": deps.runtime_events,
    }


def _build_retrieval_stage_plan(query_parameters: QueryParameters) -> TaskPlan:
    return TaskPlan(
        stage="retrieve_and_validate_evidence",
        goal="Retrieve and validate evidence for the parsed question.",
        tasks=[
            PlannedTask(
                id="t1",
                skill="retrieve_evidence",
                inputs={"anchor_only": True, "stem_type": query_parameters.stem_type},
                reason="Anchor retrieval with the full question (reranker-equivalent pass).",
            ),
            PlannedTask(
                id="t2",
                skill="judge_answerability",
                depends_on=["t1"],
                reason="Assess whether retrieved evidence can answer the question.",
            ),
        ],
        response_strategy="Store raw evidence and write short answerability notes.",
    )


def _build_followup_retrieval_plan(
    answerability: dict,
    query_parameters: QueryParameters,
) -> TaskPlan:
    return TaskPlan(
        stage="retrieve_and_validate_evidence",
        goal="Run targeted follow-up retrieval for missing anchors.",
        tasks=[
            PlannedTask(
                id="t1",
                skill="retrieve_evidence",
                inputs={
                    "missing_anchors": (answerability.get("missing_anchors", []) or [])[:6],
                    "closest_competing_options": answerability.get("closest_competing_options", []) or [],
                    "supplemental_queries": query_parameters.retrieval_queries[:3],
                    "max_rounds": 1,
                },
                reason=f"Retrieve targeted passages for {_followup_trigger_reason(answerability)}.",
            ),
            PlannedTask(
                id="t2",
                skill="judge_answerability",
                depends_on=["t1"],
                reason="Re-check answerability after targeted retrieval.",
            ),
        ],
        response_strategy="Update evidence pool and answerability status.",
    )


def _build_reasoning_stage_plan(query_parameters: QueryParameters, state: dict) -> TaskPlan:
    evidence_pool = state.get("evidence_pool", [])
    dependency_results = _flatten_stage_results(
        state.get("stage_outputs", {}).get("retrieve_and_validate_evidence", {})
    )
    if query_parameters.mode == "mcq":
        task = PlannedTask(
            id="t1",
            skill="reason_mcq_answer",
            inputs={"dependency_results": dependency_results, "evidence_pool": evidence_pool},
            reason="Select the MCQ answer from retrieved evidence.",
        )
    else:
        task = PlannedTask(
            id="t1",
            skill="compose_guidance",
            inputs={"dependency_results": dependency_results, "evidence_pool": evidence_pool},
            reason="Compose diabetes guidance from retrieved evidence.",
        )
    return TaskPlan(
        stage="reason_and_compose_answer",
        goal="Reason over validated evidence and compose the final answer.",
        tasks=[task],
        response_strategy="Use the reasoning skill report as the only source for the final response.",
    )


def _anchor_evidence_limit() -> int:
    return 5


def _followup_evidence_limit() -> int:
    return 8


def _merge_evidence_pool(
    existing: list[dict],
    results: list,
    *,
    anchor_query: str = "",
    limit: int = 5,
) -> list[dict]:
    merged = list(existing or [])
    seen = {item.get("id") for item in merged if isinstance(item, dict)}
    for result in results:
        for item in result.evidence:
            if isinstance(item, dict) and item.get("id") not in seen:
                seen.add(item.get("id"))
                merged.append(item)
    return rank_passages_for_question(merged, anchor_query=anchor_query, limit=limit)


def _append_retrieval_common_context(existing: list[dict], results: list) -> list[dict]:
    common_context = list(existing or [])
    for result in results:
        kind = "answerability" if result.skill_name == "judge_answerability" else "retrieval"
        common_context = append_result_context(common_context, result, kind=kind)
    answerability = _latest_answerability(results)
    if answerability:
        common_context = append_common_context(
            common_context,
            source="retrieve_and_validate_evidence",
            kind="answerability",
            summaries=[
                f"Evidence sufficient={answerability.get('sufficient')} with confidence={answerability.get('confidence')}.",
                f"Missing anchors: {', '.join(answerability.get('missing_anchors', [])[:5])}"
                if answerability.get("missing_anchors")
                else "No missing anchors reported.",
            ],
            importance="high",
            evidence_ids=answerability.get("evidence_ids", []),
        )
    return common_context


def _latest_answerability(results: list) -> dict | None:
    for result in reversed(results):
        if result.skill_name == "judge_answerability":
            answerability = (result.data or {}).get("answerability")
            if isinstance(answerability, dict):
                return answerability
    return None


def _should_run_followup(answerability: dict | None) -> bool:
    if not answerability:
        return False
    missing = answerability.get("missing_anchors") or []
    competitors = answerability.get("closest_competing_options") or []
    if answerability.get("sufficient") is False and (missing or competitors):
        return True
    return False


def _followup_trigger_reason(answerability: dict | None) -> str:
    if not answerability:
        return "missing answerability assessment"
    competitors = answerability.get("closest_competing_options") or []
    missing = answerability.get("missing_anchors") or []
    if answerability.get("sufficient") is False and missing:
        return "insufficient evidence with missing anchors"
    if answerability.get("sufficient") is False:
        return "insufficient evidence"
    return "targeted retrieval"


def _flatten_stage_results(stage_output: dict) -> dict:
    results = stage_output.get("results", []) if isinstance(stage_output, dict) else []
    return {result.get("task_id", f"t{idx}"): result for idx, result in enumerate(results, start=1)}
