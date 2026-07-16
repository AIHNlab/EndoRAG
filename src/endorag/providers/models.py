"""LlamaIndex model bootstrap for EndoRAG experiments."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from llama_index.core import Settings
from llama_index.core.base.embeddings.base import BaseEmbedding
from llama_index.core.llms import LLM
from llama_index.embeddings.huggingface import HuggingFaceEmbedding
from llama_index.embeddings.ollama import OllamaEmbedding
from llama_index.embeddings.openai import OpenAIEmbedding
from llama_index.llms.ollama import Ollama
from llama_index.llms.openai import OpenAI

from .ollama import is_cloud_model, ollama_api_key, ollama_headers, ollama_host

DEFAULT_LLM_PARAMS: dict[str, Any] = {
    "temperature": 0,
    "num_ctx": 128000,
    "top_p": 0.9,
    "top_k": 40,
    "seed": 42,
    "num_predict": 4096,
}
DEFAULT_EMBED_PARAMS: dict[str, Any] = {
    "temperature": 0,
    "num_ctx": 4096,
    "seed": 42,
}


@dataclass(frozen=True)
class ProviderConfig:
    llm_name: str
    embed_model_name: str | None = None
    llm_params: dict[str, Any] = field(default_factory=dict)
    embed_params: dict[str, Any] = field(default_factory=dict)


class ModelProvider:
    def __init__(self, config: ProviderConfig) -> None:
        self.config = config

    def load_llm(self) -> LLM:
        params = {**DEFAULT_LLM_PARAMS, **self.config.llm_params}
        if self.config.llm_name.startswith("gpt-"):
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                raise RuntimeError("OPENAI_API_KEY is required for OpenAI models.")
            llm: LLM = OpenAI(
                model=self.config.llm_name,
                api_key=api_key,
                request_timeout=604800,
                **{
                    key: value
                    for key, value in params.items()
                    if key in {"temperature", "seed", "top_p"}
                },
            )
        else:
            context_window = int(params.get("num_ctx", 128000))
            host = ollama_host(self.config.llm_name)
            kwargs: dict[str, Any] = {
                "model": self.config.llm_name,
                "base_url": host,
                "request_timeout": 604800,
                "context_window": context_window,
                **params,
            }
            headers = ollama_headers(self.config.llm_name)
            if headers:
                kwargs["headers"] = headers
            llm = Ollama(**kwargs)
        Settings.llm = llm
        return llm

    def load_embed_model(self) -> BaseEmbedding:
        model_name = self.config.embed_model_name
        if not model_name:
            raise ValueError("embed_model_name is required for retrieval.")
        params = {**DEFAULT_EMBED_PARAMS, **self.config.embed_params}
        if model_name == "text-embedding-3-large":
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                raise RuntimeError(
                    "OPENAI_API_KEY is required for text-embedding-3-large."
                )
            embedding: BaseEmbedding = OpenAIEmbedding(
                model=model_name,
                api_key=api_key,
                **params,
            )
        elif model_name.startswith("hf:"):
            embedding = HuggingFaceEmbedding(model_name=model_name.removeprefix("hf:"))
        else:
            host = ollama_host(model_name)
            kwargs: dict[str, Any] = {
                "model_name": model_name,
                "base_url": host,
                "request_timeout": 604800,
                "ollama_additional_kwargs": params,
                **params,
            }
            if is_cloud_model(model_name):
                kwargs["client_kwargs"] = {
                    "timeout": 604800,
                    "headers": {
                        "Authorization": f"Bearer {ollama_api_key(required=True)}"
                    },
                }
            embedding = OllamaEmbedding(**kwargs)
        Settings.embed_model = embedding
        return embedding

    def bootstrap_settings(
        self, *, include_embedding: bool
    ) -> tuple[LLM, BaseEmbedding | None]:
        llm = self.load_llm()
        embedding = self.load_embed_model() if include_embedding else None
        return llm, embedding
