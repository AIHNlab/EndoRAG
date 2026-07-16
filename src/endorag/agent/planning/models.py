from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


StageName = Literal[
    "understand_question",
    "retrieve_and_validate_evidence",
    "reason_and_compose_answer",
]

AllowedSkill = Literal[
    "mark_invalid_question",
    "analyze_mcq_stem",
    "analyze_clinical_question",
    "retrieve_evidence",
    "judge_answerability",
    "reason_mcq_answer",
    "compose_guidance",
]


class PlannedTask(BaseModel):
    id: str = Field(..., description="Short task id such as t1, t2, t3.")
    skill: AllowedSkill
    inputs: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    reason: str
    task_objective: str | None = None
    required_inputs: list[str] = Field(default_factory=list)
    expected_outputs: list[str] = Field(default_factory=list)
    context_instructions: str | None = None


class TaskPlan(BaseModel):
    stage: StageName
    goal: str
    tasks: list[PlannedTask]
    response_strategy: str


class ManagerSkillSelection(BaseModel):
    """Compact manager output; code expands this into a TaskPlan."""

    model_config = ConfigDict(extra="ignore")

    skills: list[AllowedSkill] = Field(default_factory=list)
