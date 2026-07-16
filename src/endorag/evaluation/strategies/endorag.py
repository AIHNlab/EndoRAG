"""EndoRAG agentic workflow evaluation strategy."""

from __future__ import annotations

import asyncio
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

from llama_index.core import Settings

from endorag.agent.agents.deps import EndoRAGDeps
from endorag.agent.agents.planning_manager import build_planning_manager
from endorag.agent.agents.structured_agents import (
    build_answerability_agent,
    build_arbiter_agent,
    build_baseline_selector_agent,
    build_clinical_analysis_agent,
    build_guidance_agent,
    build_reasoning_agent,
    build_stem_analysis_agent,
    build_verifier_agent,
)
from endorag.agent.orchestration.runner import run_endorag_workflow
from endorag.agent.planning.parameters import QueryParameters
from endorag.agent.skills.reason_mcq_answer import (
    normalize_mcq_letter as agent_normalize_mcq_letter,
)
from endorag.evaluation.metrics import normalize_mcq_letter
from endorag.evaluation.models import Prediction
from endorag.retrieval.registry import VectorDbRegistry
from endorag.retrieval.vector_tools import VectorTools


class EndoRAGStrategy:
    def __init__(
        self,
        deps: EndoRAGDeps,
        *,
        routing_registry: VectorDbRegistry | None = None,
        pinned_category: str | None = None,
        oracle_map_path: str | Path | None = None,
    ) -> None:
        self.deps = deps
        self.routing_registry = routing_registry
        self.pinned_category = pinned_category
        self.oracle_map_path = oracle_map_path

    def answer(
        self,
        question: str,
        context: dict[str, Any] | None = None,
    ) -> Prediction:
        routing_category = self.pinned_category
        if self.routing_registry is not None:
            routing_category = self.routing_registry.resolve_category(
                question,
                llm=Settings.llm,
                pinned_category=self.pinned_category,
                oracle_map_path=self.oracle_map_path,
            )

        # Agents, vector tools, and model clients are safe to reuse, but runtime
        # diagnostics are question-local. Fresh lists prevent earlier calls and
        # failures from leaking into later result records.
        run_deps = replace(
            self.deps,
            routing_domain=routing_category,
            runtime_events=[],
            task_executions=[],
            tool_calls=[],
            agent_outputs=[],
        )
        workflow_result = asyncio.run(
            run_endorag_workflow(question, deps=run_deps)
        )
        actual_output = extract_mcq_letter(
            workflow_result.get("final_answer", ""),
            workflow_result=workflow_result,
        )
        retrieval_context = agentic_retrieval_context(workflow_result)
        flow_diagnostics = agentic_workflow_diagnostics(workflow_result)
        if routing_category:
            flow_diagnostics["routed_category"] = routing_category
        workflow_error = agentic_workflow_error_message(workflow_result)
        if workflow_error:
            flow_diagnostics["workflow_error"] = workflow_error
        return Prediction(
            actual_output=actual_output,
            retrieval_context=retrieval_context,
            routing_category=flow_diagnostics.get("routed_category"),
            flow_diagnostics=flow_diagnostics,
        )


def build_endorag_deps(
    vector_tools: VectorTools,
    *,
    llm_name: str,
    base_url: str | None = None,
    seed: int = 42,
) -> EndoRAGDeps:
    return EndoRAGDeps(
        vector_tools=vector_tools,
        manager_agent=build_planning_manager(
            model_name=llm_name, base_url=base_url, seed=seed
        ),
        stem_analysis_agent=build_stem_analysis_agent(
            model_name=llm_name, base_url=base_url, seed=seed
        ),
        clinical_analysis_agent=build_clinical_analysis_agent(
            model_name=llm_name, base_url=base_url, seed=seed
        ),
        answerability_agent=build_answerability_agent(
            model_name=llm_name, base_url=base_url, seed=seed
        ),
        reasoning_agent=build_reasoning_agent(
            model_name=llm_name, base_url=base_url, seed=seed
        ),
        verifier_agent=build_verifier_agent(
            model_name=llm_name, base_url=base_url, seed=seed
        ),
        baseline_selector_agent=build_baseline_selector_agent(
            model_name=llm_name, base_url=base_url, seed=seed
        ),
        arbiter_agent=build_arbiter_agent(
            model_name=llm_name, base_url=base_url, seed=seed
        ),
        guidance_agent=build_guidance_agent(
            model_name=llm_name, base_url=base_url, seed=seed
        ),
    )


def extract_mcq_letter(output: object, workflow_result: dict[str, Any] | None = None) -> str:
    if workflow_result:
        params = QueryParameters.model_validate(
            workflow_result.get("query_parameters") or {}
        )
        reason_stage = (workflow_result.get("stage_outputs") or {}).get(
            "reason_and_compose_answer", {}
        )
        for skill_result in reversed(reason_stage.get("results", []) or []):
            if not isinstance(skill_result, dict):
                continue
            if skill_result.get("skill_name") != "reason_mcq_answer":
                continue
            mcq = (skill_result.get("data") or {}).get("mcq_answer") or {}
            selected = agent_normalize_mcq_letter(params, mcq.get("selected_answer", ""))
            if selected in {"a", "b", "c", "d", "e"}:
                return selected

    normalized = normalize_mcq_letter(output)
    if normalized:
        return normalized

    text = str(output or "").strip().lower()
    match = re.search(r"answer:\s*\**\s*([a-e])\b", text)
    if match:
        return match.group(1)
    match = re.search(r"\b([a-e])[\).:]\s+", text)
    if match:
        return match.group(1)
    return ""


def agentic_retrieval_context(result: dict[str, Any]) -> list[dict[str, Any]]:
    contexts: list[dict[str, Any]] = []
    for passage in result.get("evidence_pool") or []:
        if not isinstance(passage, dict):
            continue
        contexts.append(
            {
                "source": passage.get("source", "Unknown source"),
                "score": passage.get("score"),
                "query": passage.get("query"),
                "domain": passage.get("domain"),
                "retrieval_round": passage.get("retrieval_round"),
                "text": passage.get("text", ""),
                "metadata": passage.get("metadata", {}),
            }
        )
    return contexts


def agentic_workflow_diagnostics(result: dict[str, Any]) -> dict[str, Any]:
    query_parameters = result.get("query_parameters") or {}
    run_metadata = result.get("run_metadata") or {}
    provenance = (query_parameters.get("provenance") or [{}])[0]
    shared_state = result.get("shared_execution_state") or {}
    mcq_state = shared_state.get("mcq") or {}
    retrieval_state = shared_state.get("retrieval") or {}
    reasoning = latest_agentic_reasoning(result) or {}
    decision_trace = reasoning.get("decision_trace") or mcq_state.get("decision_trace") or {}
    return {
        "workflow": "endorag",
        "trace_id": result.get("trace_id"),
        "mode": query_parameters.get("mode"),
        "input_format": run_metadata.get("input_format") or provenance.get("input_format"),
        "stem_type": query_parameters.get("stem_type"),
        "polarity": query_parameters.get("polarity"),
        "routed_category": result.get("routed_category")
        or query_parameters.get("suspected_domain")
        or "Unknown",
        "stage_plans": result.get("stage_plans", {}),
        "stage_outputs": result.get("stage_outputs", {}),
        "validation_errors": result.get("validation_errors", []),
        "validation_warnings": result.get("validation_warnings", []),
        "tool_calls": result.get("tool_calls", []),
        "agent_outputs": result.get("agent_outputs", []),
        "task_executions": result.get("task_executions", []),
        "final_response": result.get("final_response", {}),
        "reasoning_mode": reasoning.get("reasoning_mode") or shared_state.get("reasoning_mode"),
        "prompt_variant": reasoning.get("prompt_variant"),
        "decision_context": reasoning.get("decision_context") or mcq_state.get("decision_context"),
        "reconciliation_notes": reasoning.get("reconciliation_notes") or [],
        "verifier": reasoning.get("verifier") or mcq_state.get("verifier") or {},
        "agentic_selected_answer": reasoning.get("agentic_selected_answer")
        or decision_trace.get("agentic_selected_answer"),
        "baseline_selector": reasoning.get("baseline_selector") or {},
        "baseline_selector_answer": reasoning.get("baseline_selector_answer")
        or decision_trace.get("baseline_selector_answer"),
        "arbiter": reasoning.get("arbiter") or {},
        "arbiter_answer": reasoning.get("arbiter_answer") or decision_trace.get("arbiter_answer"),
        "arbiter_used": reasoning.get("arbiter_used") or decision_trace.get("arbiter_used") or False,
        "final_decision_source": reasoning.get("final_decision_source")
        or decision_trace.get("final_decision_source"),
        "selected_evidence_ids": reasoning.get("selected_evidence_ids") or [],
        "followup_triggers": retrieval_state.get("followup_triggers") or [],
    }


def agentic_workflow_error_message(result: dict[str, Any]) -> str | None:
    validation_errors = result.get("validation_errors") or []
    if validation_errors:
        return "Validation error: " + "; ".join(str(error) for error in validation_errors)

    for call in result.get("tool_calls") or []:
        if isinstance(call, dict) and call.get("success") is False:
            return (
                f"Tool call failed: {call.get('tool', 'unknown tool')} "
                f"{call.get('error') or call.get('args') or ''}"
            )

    for agent_output in result.get("agent_outputs") or []:
        if isinstance(agent_output, dict) and agent_output.get("success") is False:
            if agent_output.get("recoverable") is True:
                continue
            return (
                f"Agent failed: {agent_output.get('agent', 'unknown agent')} "
                f"{agent_output.get('error') or ''}"
            )

    stage_outputs = result.get("stage_outputs") or {}
    for stage_name, stage_output in stage_outputs.items():
        for skill_result in (stage_output or {}).get("results", []):
            if isinstance(skill_result, dict) and skill_result.get("status") == "failed":
                return (
                    f"Skill failed in {stage_name}: "
                    f"{skill_result.get('skill_name')} - {skill_result.get('summary')}"
                )
    return None


def latest_agentic_reasoning(result: dict[str, Any]) -> dict[str, Any] | None:
    reason_stage = (result.get("stage_outputs") or {}).get("reason_and_compose_answer", {})
    for skill_result in reversed(reason_stage.get("results", []) or []):
        if skill_result.get("skill_name") != "reason_mcq_answer":
            continue
        mcq = (skill_result.get("data") or {}).get("mcq_answer") or {}
        data = skill_result.get("data") or {}
        return {
            "status": skill_result.get("status"),
            "summary": skill_result.get("summary"),
            "selected_answer": mcq.get("selected_answer"),
            "confidence": mcq.get("confidence"),
            "limitations": skill_result.get("limitations") or [],
            "reasoning_mode": data.get("reasoning_mode"),
            "prompt_variant": data.get("prompt_variant"),
            "decision_context": data.get("decision_context"),
            "reconciliation_notes": data.get("reconciliation_notes") or [],
            "verifier": data.get("verifier") or {},
            "agentic_selected_answer": data.get("agentic_selected_answer"),
            "baseline_selector": data.get("baseline_selector") or {},
            "baseline_selector_answer": data.get("baseline_selector_answer"),
            "arbiter": data.get("arbiter") or {},
            "arbiter_answer": data.get("arbiter_answer"),
            "arbiter_used": data.get("arbiter_used") or False,
            "final_decision_source": data.get("final_decision_source"),
            "decision_trace": {
                "agentic_selected_answer": data.get("agentic_selected_answer"),
                "baseline_selector_answer": data.get("baseline_selector_answer"),
                "arbiter_answer": data.get("arbiter_answer"),
                "arbiter_used": data.get("arbiter_used") or False,
                "final_decision_source": data.get("final_decision_source"),
            },
            "selected_evidence_ids": data.get("selected_evidence_ids") or [],
        }
    return None
