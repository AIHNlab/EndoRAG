from __future__ import annotations

import uuid
from typing import Any

from endorag.agent.agents.deps import EndoRAGDeps
from endorag.agent.orchestration.graph import build_graph
from endorag.agent.planning.question_normalize import standardize_exam_question


async def run_endorag_workflow(
    question: str,
    deps: EndoRAGDeps | None = None,
    *,
    trace_id: str | None = None,
    config: dict | None = None,
) -> dict[str, Any]:
    deps = deps or EndoRAGDeps()
    standardized = standardize_exam_question(question)
    state = {
        "trace_id": trace_id or str(uuid.uuid4()),
        "question": standardized.canonical,
        "original_question": standardized.original,
        "run_metadata": {
            "question": standardized.canonical,
            "original_question": standardized.original,
            "input_format": standardized.format,
        },
        "query_parameters": {},
        "common_context": [],
        "shared_execution_state": {},
        "stage_plans": {},
        "stage_outputs": {},
        "evidence_pool": [],
        "validation_errors": [],
        "validation_warnings": [],
        "runtime_events": deps.runtime_events,
        "task_executions": deps.task_executions,
        "tool_calls": deps.tool_calls,
        "agent_outputs": deps.agent_outputs,
    }
    graph = build_graph(deps=deps)
    if hasattr(graph, "ainvoke"):
        return await graph.ainvoke(state, config=config or {"configurable": {"thread_id": state["trace_id"]}})
    return graph.invoke(state, config=config)
