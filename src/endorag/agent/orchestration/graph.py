from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from endorag.agent.agents.deps import EndoRAGDeps
from endorag.agent.orchestration.nodes import (
    handle_error_node,
    reason_and_compose_answer_node,
    retrieve_and_validate_evidence_node,
    should_continue_after_question_understanding,
    should_continue_after_retrieval,
    understand_question_node,
)
from endorag.agent.orchestration.state import EndoRAGState


NodeFn = Callable[[dict], Awaitable[dict]]


def build_graph(
    checkpointer: Any | None = None,
    deps: EndoRAGDeps | None = None,
):
    """Build the required LangGraph workflow."""
    graph = StateGraph(EndoRAGState)
    graph.add_node("understand_question", _with_transient_deps(understand_question_node, deps))
    graph.add_node(
        "retrieve_and_validate_evidence",
        _with_transient_deps(retrieve_and_validate_evidence_node, deps),
    )
    graph.add_node("reason_and_compose_answer", _with_transient_deps(reason_and_compose_answer_node, deps))
    graph.add_node("error", _with_transient_deps(handle_error_node, deps))
    graph.add_edge(START, "understand_question")
    graph.add_conditional_edges(
        "understand_question",
        should_continue_after_question_understanding,
        {"retrieve": "retrieve_and_validate_evidence", "end": END, "error": "error"},
    )
    graph.add_conditional_edges(
        "retrieve_and_validate_evidence",
        should_continue_after_retrieval,
        {"reason": "reason_and_compose_answer", "end": END, "error": "error"},
    )
    graph.add_edge("reason_and_compose_answer", END)
    graph.add_edge("error", END)
    return graph.compile(checkpointer=checkpointer or MemorySaver())


def _with_transient_deps(node: NodeFn, deps: EndoRAGDeps | None) -> NodeFn:
    async def wrapped(state: dict) -> dict:
        if deps is None:
            return await node(state)
        result = await node({**state, "_deps": deps})
        result.pop("_deps", None)
        return result

    return wrapped
