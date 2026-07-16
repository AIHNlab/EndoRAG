"""Pydantic AI client for Ollama's OpenAI-compatible endpoint."""

from __future__ import annotations

from typing import Any

from endorag.providers.ollama import (
    is_cloud_model,
    ollama_api_key,
    ollama_host,
)


def get_model(model_name: str, base_url: str | None = None):
    try:
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.providers.openai import OpenAIProvider
    except ImportError as exc:
        raise RuntimeError(
            "Install pydantic-ai-slim[openai] to run structured EndoRAG agents."
        ) from exc

    resolved_base_url = base_url or ollama_host(
        model_name,
        openai_compatible=True,
    )
    api_key = (
        ollama_api_key(required=True)
        if is_cloud_model(model_name)
        else "ollama-local"
    )
    return OpenAIChatModel(
        model_name,
        provider=OpenAIProvider(
            base_url=resolved_base_url,
            api_key=api_key,
        ),
    )


def ollama_structured_output(output_model: type) -> Any:
    from pydantic_ai.output import PromptedOutput

    return PromptedOutput(
        output_model,
        description=(
            "Return one JSON object matching the schema. "
            "Use empty lists for unknown list fields and null for unknown optional fields. "
            "Do not wrap the JSON in markdown code fences."
        ),
    )
