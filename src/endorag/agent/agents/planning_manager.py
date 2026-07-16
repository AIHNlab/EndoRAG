from __future__ import annotations

from endorag.providers.settings import get_settings
from endorag.agent.planning.models import ManagerSkillSelection
from endorag.agent.planning.prompts import MANAGER_SYSTEM_PROMPT


def build_planning_manager(
    model_name: str | None = None,
    base_url: str | None = None,
    seed: int = 42,
):
    """Create the optional PydanticAI planning manager."""
    try:
        from pydantic_ai import Agent
        from pydantic_ai.settings import ModelSettings
    except ImportError as exc:
        raise RuntimeError(
            "pydantic-ai is not installed. Deterministic fallback planning remains available."
        ) from exc

    from endorag.agent.agents.deps import EndoRAGDeps
    from endorag.agent.agents.llm_client import get_model, ollama_structured_output

    settings = get_settings()
    return Agent(
        get_model(
            model_name or settings.ollama_model,
            base_url,
        ),
        output_type=ollama_structured_output(ManagerSkillSelection),
        deps_type=EndoRAGDeps,
        retries={"output": 3},
        model_settings=ModelSettings(temperature=0.0, seed=seed),
        system_prompt=MANAGER_SYSTEM_PROMPT,
    )
