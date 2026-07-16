from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from endorag.providers.settings import EndoRAGSettings, get_settings
from endorag.retrieval.vector_tools import VectorTools


@dataclass
class EndoRAGDeps:
    vector_tools: VectorTools | None = None
    routing_domain: str | None = None
    manager_agent: Any | None = None
    response_composer_agent: Any | None = None
    stem_analysis_agent: Any | None = None
    clinical_analysis_agent: Any | None = None
    answerability_agent: Any | None = None
    reasoning_agent: Any | None = None
    verifier_agent: Any | None = None
    baseline_selector_agent: Any | None = None
    arbiter_agent: Any | None = None
    guidance_agent: Any | None = None
    settings: EndoRAGSettings = field(default_factory=get_settings)
    runtime_events: list[dict] = field(default_factory=list)
    task_executions: list[dict] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)
    agent_outputs: list[dict] = field(default_factory=list)

    def log_tool_call(self, event: dict) -> None:
        self.tool_calls.append(event)

    def log_agent_output(self, event: dict) -> None:
        self.agent_outputs.append(event)

    def log_task_execution(self, event: dict) -> None:
        self.task_executions.append(event)
