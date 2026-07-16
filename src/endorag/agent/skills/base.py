from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field


SkillStatus = Literal[
    "success",
    "partial",
    "failed",
    "invalid_question",
    "insufficient_context",
]


class StatePatch(BaseModel):
    op: Literal["set", "append", "upsert"]
    key: str
    value: Any = None
    source: str | None = None


class SkillContext(BaseModel):
    use_case: str | None = None
    run_metadata: dict[str, Any] = Field(default_factory=dict)
    query_parameters: dict[str, Any] = Field(default_factory=dict)
    shared_state: dict[str, Any] = Field(default_factory=dict)
    common_context: list[dict[str, Any]] = Field(default_factory=list)
    working_context: str = ""


class SkillResult(BaseModel):
    task_id: str
    skill_name: str
    status: SkillStatus
    summary: str
    data: dict[str, Any] = Field(default_factory=dict)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    provenance: list[dict[str, Any]] = Field(default_factory=list)
    state_patches: list[StatePatch] = Field(default_factory=list)
    derived_parameters: dict[str, Any] = Field(default_factory=dict)
    context_updates: list[str] = Field(default_factory=list)


class EndoRAGSkill(Protocol):
    name: str
    description: str
    input_model: type[BaseModel]

    async def run(
        self,
        task_id: str,
        inputs: BaseModel,
        context: SkillContext,
        deps: Any,
    ) -> SkillResult:
        ...
