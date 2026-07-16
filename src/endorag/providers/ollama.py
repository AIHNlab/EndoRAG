"""Ollama endpoint and credential resolution."""

from __future__ import annotations

import os

LOCAL_BASE_URL = "http://localhost:11434"
CLOUD_BASE_URL = "https://ollama.com"


class ProviderConfigurationError(RuntimeError):
    """Raised when a requested provider cannot be configured safely."""


def is_cloud_model(model_name: str) -> bool:
    return model_name.endswith("-cloud") or model_name.endswith(":cloud")


def ollama_api_key(*, required: bool = False) -> str | None:
    key = os.getenv("ENDORAG_OLLAMA_API_KEY") or os.getenv("OLLAMA_API_KEY")
    if required and not key:
        raise ProviderConfigurationError(
            "A cloud Ollama model requires ENDORAG_OLLAMA_API_KEY or OLLAMA_API_KEY."
        )
    return key


def ollama_host(model_name: str, *, openai_compatible: bool = False) -> str:
    if is_cloud_model(model_name):
        base_url = os.getenv("ENDORAG_OLLAMA_CLOUD_BASE_URL", CLOUD_BASE_URL)
    else:
        base_url = os.getenv(
            "ENDORAG_OLLAMA_BASE_URL",
            os.getenv("OLLAMA_HOST", LOCAL_BASE_URL),
        )
    normalized = base_url.rstrip("/")
    # Native Ollama clients call ``/api/chat``. A trailing ``/v1`` produces
    # ``/v1/api/chat`` (404 on ollama.com). Only keep ``/v1`` for OpenAI-compatible callers.
    if not openai_compatible and normalized.endswith("/v1"):
        normalized = normalized[: -len("/v1")].rstrip("/") or normalized
    if openai_compatible and not normalized.endswith("/v1"):
        normalized = f"{normalized}/v1"
    return normalized


def ollama_headers(model_name: str) -> dict[str, str] | None:
    if not is_cloud_model(model_name):
        return None
    return {"Authorization": f"Bearer {ollama_api_key(required=True)}"}
