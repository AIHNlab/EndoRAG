from __future__ import annotations

import time
from copy import deepcopy
from typing import Any

from endorag.agent.planning.models import PlannedTask, TaskPlan
from endorag.agent.skills.base import SkillContext, SkillResult, StatePatch
from endorag.agent.skills.registry import SKILL_REGISTRY


def topological_sort(tasks: list[PlannedTask]) -> list[PlannedTask]:
    by_id = {task.id: task for task in tasks}
    visited: set[str] = set()
    visiting: set[str] = set()
    result: list[PlannedTask] = []

    def visit(task_id: str) -> None:
        if task_id in visited:
            return
        if task_id in visiting:
            raise ValueError(f"Cycle detected at task {task_id}")
        visiting.add(task_id)
        task = by_id[task_id]
        for dep in task.depends_on:
            if dep not in by_id:
                raise ValueError(f"Unknown dependency {dep} for task {task_id}")
            visit(dep)
        visiting.remove(task_id)
        visited.add(task_id)
        result.append(task)

    for task in tasks:
        visit(task.id)
    return result


async def execute_plan(plan: TaskPlan, context: SkillContext, deps: Any) -> list[SkillResult]:
    results_by_id: dict[str, SkillResult] = {}

    for task in topological_sort(plan.tasks):
        start = time.perf_counter()
        skill = SKILL_REGISTRY[task.skill]
        inputs = dict(task.inputs)
        dependency_results = {
            dep: results_by_id[dep].model_dump()
            for dep in task.depends_on
            if dep in results_by_id
        }
        if "dependency_results" in skill.input_model.model_fields and "dependency_results" not in inputs:
            inputs["dependency_results"] = dependency_results

        task_context = SkillContext(
            use_case=context.use_case,
            run_metadata=deepcopy(context.run_metadata),
            query_parameters=deepcopy(context.query_parameters),
            shared_state=deepcopy(context.shared_state),
            common_context=deepcopy(context.common_context),
            working_context=context.working_context,
        )
        input_obj = skill.input_model(**inputs)
        result = await skill.run(task.id, input_obj, task_context, deps)

        context.run_metadata = task_context.run_metadata
        for patch in result.state_patches:
            _apply_patch(context, patch)
        if result.derived_parameters:
            context.query_parameters.update(result.derived_parameters)
        if result.context_updates:
            _append_working_context(context, task.skill, result.context_updates)

        results_by_id[task.id] = result
        if hasattr(deps, "log_task_execution"):
            deps.log_task_execution(
                {
                    "stage": plan.stage,
                    "task_id": task.id,
                    "skill": task.skill,
                    "status": result.status,
                    "latency_ms": (time.perf_counter() - start) * 1000,
                }
            )
        if result.status in {"invalid_question", "insufficient_context"}:
            break

    return list(results_by_id.values())


def _apply_patch(context: SkillContext, patch: StatePatch) -> None:
    target = context.run_metadata if patch.key.startswith("run_metadata.") else context.shared_state
    key = patch.key.removeprefix("run_metadata.")
    parts = key.split(".")
    cursor = target
    for part in parts[:-1]:
        cursor = cursor.setdefault(part, {})
    leaf = parts[-1]
    if patch.op == "set":
        cursor[leaf] = patch.value
    elif patch.op == "append":
        cursor.setdefault(leaf, []).append(patch.value)
    elif patch.op == "upsert":
        cursor.setdefault(leaf, {}).update(patch.value or {})


def _append_working_context(context: SkillContext, source: str, updates: list[str]) -> None:
    clean = [item.strip() for item in updates if item and item.strip()]
    if not clean:
        return
    section = "\n".join(f"- [{source}] {item}" for item in clean)
    context.working_context = f"{context.working_context}\n\nFindings and updates:\n{section}".strip()
