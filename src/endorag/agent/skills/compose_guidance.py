from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from endorag.agent.skills.base import SkillContext, SkillResult


class ComposeGuidanceInput(BaseModel):
    dependency_results: dict = Field(default_factory=dict)
    evidence_pool: list[dict] = Field(default_factory=list)


class GuidanceOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    answer_markdown: str
    confidence: str
    assumptions: list[str] = Field(default_factory=list)
    safety_notes: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class ComposeGuidanceSkill:
    name = "compose_guidance"
    description = "Compose non-MCQ diabetes guidance from retrieved evidence."
    input_model = ComposeGuidanceInput

    async def run(
        self,
        task_id: str,
        inputs: ComposeGuidanceInput,
        context: SkillContext,
        deps,
    ) -> SkillResult:
        passages = _collect_passages(inputs.dependency_results) or inputs.evidence_pool
        if not passages:
            return SkillResult(
                task_id=task_id,
                skill_name=self.name,
                status="partial",
                summary="No evidence was available for guidance.",
                limitations=["No retrieved passages available."],
            )

        agent = getattr(deps, "guidance_agent", None)
        if agent is not None:
            prompt = _build_guidance_prompt(context, passages)
            try:
                response = await agent.run(prompt, deps=deps)
                output = response.output
            except Exception as exc:  # noqa: BLE001
                if hasattr(deps, "log_agent_output"):
                    deps.log_agent_output({"agent": "guidance", "success": False, "error": repr(exc)})
                return SkillResult(
                    task_id=task_id,
                    skill_name=self.name,
                    status="failed",
                    summary=f"PydanticAI guidance_agent failed: {exc!r}",
                    limitations=["Guidance composition failed; no fallback answer was used."],
                )
        else:
            output = _fallback_guidance(passages)

        return SkillResult(
            task_id=task_id,
            skill_name=self.name,
            status="success",
            summary="Composed diabetes guidance from retrieved evidence.",
            data={"guidance": output.model_dump()},
            evidence=passages,
            limitations=output.assumptions + output.safety_notes,
            context_updates=[output.answer_markdown[:500]],
        )


def _collect_passages(dependency_results: dict) -> list[dict]:
    passages: list[dict] = []
    for result in dependency_results.values():
        if isinstance(result, dict):
            passages.extend(result.get("evidence") or [])
            passages.extend((result.get("data") or {}).get("passages") or [])
    return passages


def _fallback_guidance(passages: list[dict]) -> GuidanceOutput:
    evidence_ids = [str(p.get("id")) for p in passages[:5] if p.get("id")]
    return GuidanceOutput(
        answer_markdown=(
            "Evidence was retrieved, but no guidance synthesis agent is configured. "
            "Review the cited passages directly before using this clinically."
        ),
        confidence="low",
        safety_notes=["This workflow does not replace clinician judgment."],
        evidence_ids=evidence_ids,
    )


def _build_guidance_prompt(context: SkillContext, passages: list[dict]) -> str:
    evidence = "\n\n".join(
        f"[{p.get('id')}] Source: {p.get('source')}\n{p.get('text')}"
        for p in passages[:16]
    )
    return f"""
Working context:
{context.working_context}

Query parameters:
{context.query_parameters}

Evidence:
{evidence}

Return structured GuidanceOutput.
"""
