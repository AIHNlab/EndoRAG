from __future__ import annotations

from typing import Any, TypedDict


class EndoRAGState(TypedDict, total=False):
    trace_id: str
    question: str
    mode: str
    run_metadata: dict[str, Any]
    query_parameters: dict[str, Any]
    common_context: list[dict[str, Any]]
    shared_execution_state: dict[str, Any]
    working_context: str
    stage_plans: dict[str, Any]
    stage_outputs: dict[str, Any]
    evidence_pool: list[dict[str, Any]]
    validation_errors: list[str]
    validation_warnings: list[str]
    final_response: Any
    final_answer: str
    runtime_events: list[dict[str, Any]]
    task_executions: list[dict[str, Any]]
    tool_calls: list[dict[str, Any]]
    agent_outputs: list[dict[str, Any]]
    error: str | None
