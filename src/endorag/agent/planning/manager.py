from __future__ import annotations

from endorag.agent.planning.models import (
    AllowedSkill,
    ManagerSkillSelection,
    PlannedTask,
    StageName,
    TaskPlan,
)
from endorag.agent.planning.parameters import QueryParameters, extract_query_parameters
from endorag.agent.planning.prompts import MANAGER_USER_TEMPLATE, STAGE_ALLOWED_SKILLS
from endorag.agent.skills.base import SkillContext


def _build_plan_from_skill_sequence(
    stage: StageName,
    selected_skills: list[AllowedSkill],
    context: SkillContext,
    query_parameters: QueryParameters,
) -> TaskPlan:
    allowed = set(STAGE_ALLOWED_SKILLS[stage])
    ordered_skills: list[AllowedSkill] = []
    for skill in selected_skills:
        if skill in allowed and skill not in ordered_skills:
            ordered_skills.append(skill)

    if not ordered_skills:
        return _fallback_plan_for_stage(stage, context, query_parameters)

    tasks: list[PlannedTask] = []
    for idx, skill in enumerate(ordered_skills, start=1):
        task_id = f"t{idx}"
        inputs: dict[str, object] = {}
        depends_on: list[str] = []

        if skill == "analyze_mcq_stem":
            inputs = {
                "stem_type": query_parameters.stem_type,
                "options": [option.model_dump() for option in query_parameters.options],
                "refined_query_seed": query_parameters.refined_query_seed,
            }
        elif skill == "analyze_clinical_question":
            inputs = {"refined_query_seed": query_parameters.refined_query_seed}
        elif skill == "mark_invalid_question":
            inputs = {"reason": _invalid_reason(query_parameters)}
        elif skill == "judge_answerability":
            retrieval_task = _find_task(tasks, "retrieve_evidence")
            depends_on = [retrieval_task] if retrieval_task else []

        tasks.append(
            PlannedTask(
                id=task_id,
                skill=skill,
                inputs=inputs,
                depends_on=depends_on,
                reason="Selected by planning manager.",
            )
        )

    return TaskPlan(
        stage=stage,
        goal=f"Complete the {stage} workflow stage.",
        tasks=tasks,
        response_strategy="Write structured stage outputs and short common-context updates.",
    )


async def create_task_plan(
    stage: StageName,
    question: str,
    context: SkillContext,
    deps,
    query_parameters: QueryParameters | None = None,
    common_context: str = "",
) -> TaskPlan:
    query_parameters = query_parameters or extract_query_parameters(question)
    # Structural MCQ parse is authoritative; do not let the manager mark valid exams invalid.
    if (
        stage == "understand_question"
        and query_parameters.mode == "mcq"
        and len(query_parameters.options) >= 2
    ):
        return _fallback_plan_for_stage(stage, context, query_parameters)

    manager_agent = getattr(deps, "manager_agent", None)
    if manager_agent is not None:
        prompt = MANAGER_USER_TEMPLATE.format(
            stage=stage,
            allowed_skills=STAGE_ALLOWED_SKILLS[stage],
            question=question,
            query_parameters=query_parameters.model_dump(),
            common_context=common_context,
            working_context=context.working_context,
            run_metadata=context.run_metadata,
        )
        try:
            result = await manager_agent.run(prompt, deps=deps)
            output = getattr(result, "output", None)
            if isinstance(output, ManagerSkillSelection):
                return _build_plan_from_skill_sequence(
                    stage, output.skills, context, query_parameters
                )
        except Exception as exc:  # noqa: BLE001
            if hasattr(deps, "log_agent_output"):
                deps.log_agent_output(
                    {
                        "agent": "planning_manager",
                        "success": False,
                        "error": repr(exc),
                    }
                )

    return _fallback_plan_for_stage(stage, context, query_parameters)

def _fallback_plan_for_stage(
    stage: StageName,
    context: SkillContext,
    query_parameters: QueryParameters,
) -> TaskPlan:
    if stage == "understand_question":
        if query_parameters.mode == "mcq" and len(query_parameters.options) >= 2:
            return _build_plan_from_skill_sequence(
                stage, ["analyze_mcq_stem"], context, query_parameters
            )
        if query_parameters.mode == "clinical_guidance":
            return _build_plan_from_skill_sequence(
                stage, ["analyze_clinical_question"], context, query_parameters
            )
        return _invalid_question_plan(_invalid_reason(query_parameters))

    if stage == "retrieve_and_validate_evidence":
        return _build_plan_from_skill_sequence(
            stage,
            ["retrieve_evidence", "judge_answerability"],
            context,
            query_parameters,
        )

    if query_parameters.mode == "mcq":
        return _build_plan_from_skill_sequence(
            stage, ["reason_mcq_answer"], context, query_parameters
        )
    return _build_plan_from_skill_sequence(
        stage, ["compose_guidance"], context, query_parameters
    )


def _find_task(tasks: list[PlannedTask], skill_name: str) -> str | None:
    return next((task.id for task in tasks if task.skill == skill_name), None)


def _invalid_reason(query_parameters: QueryParameters) -> str:
    if query_parameters.mode == "mcq":
        return "The input appears to be an MCQ but does not include at least two parseable answer options."
    return "Input could not be classified as an MCQ or diabetes management question."


def _invalid_question_plan(reason: str) -> TaskPlan:
    return TaskPlan(
        stage="understand_question",
        goal="Mark input question as invalid or insufficient.",
        tasks=[
            PlannedTask(
                id="t1",
                skill="mark_invalid_question",
                inputs={"reason": reason},
                reason="Missing critical detail.",
            )
        ],
        response_strategy="Return an invalid-question result with limitations.",
    )
