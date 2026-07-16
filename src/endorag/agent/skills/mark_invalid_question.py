from pydantic import BaseModel

from endorag.agent.skills.base import SkillContext, SkillResult


class MarkInvalidQuestionInput(BaseModel):
    reason: str


class MarkInvalidQuestionSkill:
    name = "mark_invalid_question"
    description = "Mark an input question as invalid or insufficient for single-shot processing."
    input_model = MarkInvalidQuestionInput

    async def run(
        self,
        task_id: str,
        inputs: MarkInvalidQuestionInput,
        context: SkillContext,
        deps,
    ) -> SkillResult:
        del context, deps
        return SkillResult(
            task_id=task_id,
            skill_name=self.name,
            status="invalid_question",
            summary=inputs.reason,
            data={"invalid_reason": inputs.reason},
            limitations=[inputs.reason],
            context_updates=[f"Input marked invalid: {inputs.reason}"],
        )
