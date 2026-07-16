from __future__ import annotations

from endorag.agent.planning.models import StageName, TaskPlan
from endorag.agent.planning.parameters import QueryParameters, extract_query_parameters
from endorag.agent.planning.prompts import STAGE_ALLOWED_SKILLS
from endorag.agent.skills.base import SkillContext
from endorag.agent.skills.registry import SKILL_REGISTRY


def validate_plan(
    plan: TaskPlan,
    context: SkillContext,
    question: str | None = None,
    query_parameters: QueryParameters | None = None,
    stage: StageName | None = None,
) -> tuple[list[str], list[str]]:
    del context
    errors: list[str] = []
    warnings: list[str] = []

    active_stage = stage or plan.stage
    if not plan.tasks:
        return [f"{active_stage} task plan has no tasks."], []

    task_ids = {task.id for task in plan.tasks}
    skills = [task.skill for task in plan.tasks]
    allowed = set(STAGE_ALLOWED_SKILLS[active_stage])

    for task in plan.tasks:
        if task.skill not in SKILL_REGISTRY:
            errors.append(f"Unknown skill: {task.skill}")
        if task.skill not in allowed:
            errors.append(f"Skill {task.skill} is not allowed in stage {active_stage}")
        for dep in task.depends_on:
            if dep not in task_ids:
                errors.append(f"Task {task.id} depends on unknown task {dep}")

    if has_cycle(plan):
        errors.append("Task plan has cyclic dependencies.")

    qp = query_parameters or extract_query_parameters(question or "")
    if active_stage == "understand_question" and qp.mode == "mcq":
        if "analyze_mcq_stem" not in skills and "mark_invalid_question" not in skills:
            errors.append("understand_question MCQ plan must include analyze_mcq_stem.")
        if len(qp.options) < 2 and "mark_invalid_question" not in skills:
            errors.append("MCQ plan requires at least two parsed answer options.")

    if active_stage == "understand_question" and qp.mode == "clinical_guidance":
        if "analyze_clinical_question" not in skills and "mark_invalid_question" not in skills:
            errors.append("understand_question clinical guidance plan must include analyze_clinical_question.")

    if active_stage == "retrieve_and_validate_evidence":
        if "retrieve_evidence" not in skills:
            errors.append("retrieve_and_validate_evidence must retrieve evidence.")
        retrieval_task = next(
            (task.id for task in plan.tasks if task.skill == "retrieve_evidence"),
            None,
        )
        for task in plan.tasks:
            if task.skill == "judge_answerability" and retrieval_task not in task.depends_on:
                errors.append("judge_answerability must depend on retrieve_evidence.")

    if active_stage == "reason_and_compose_answer" and qp.mode == "mcq":
        if "reason_mcq_answer" not in skills:
            errors.append("reason_and_compose_answer MCQ plan must include reason_mcq_answer.")

    if qp.polarity == "except" and active_stage == "reason_and_compose_answer":
        warnings.append("EXCEPT/NOT polarity detected; reasoning must explicitly apply outlier logic.")
    if qp.mode == "clinical_guidance" and qp.open_variables:
        warnings.append("Clinical guidance has open variables; final answer should state limitations.")

    return errors, warnings


def has_cycle(plan: TaskPlan) -> bool:
    graph = {task.id: set(task.depends_on) for task in plan.tasks}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        for dep in graph.get(node, set()):
            if visit(dep):
                return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(visit(node) for node in graph)


def validation_errors_to_invalid_result(errors: list[str]) -> str:
    joined = "\n".join(errors).lower()
    if "answer options" in joined:
        return "Invalid input: the full MCQ stem and answer options are required."
    if "clinical details" in joined or "open variables" in joined:
        return "Insufficient context: the input lacks clinical details needed for a reliable answer."
    return "Invalid input: the question cannot be processed safely."
