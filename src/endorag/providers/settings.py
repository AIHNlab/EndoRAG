"""Environment-backed EndoRAG workflow settings."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class EndoRAGSettings:
    ollama_model: str = "mistral-nemo:latest"
    ollama_base_url: str = "http://localhost:11434"
    max_retrieval_rounds: int = 2
    retrieval_top_k: int = 5
    chroma_root: str = ""
    collection_name: str = "quickstart"
    source_dir: str = ""


def get_settings() -> EndoRAGSettings:
    return EndoRAGSettings(
        ollama_model=os.getenv("ENDORAG_OLLAMA_MODEL", "mistral-nemo:latest"),
        ollama_base_url=os.getenv(
            "ENDORAG_OLLAMA_BASE_URL", "http://localhost:11434"
        ),
        max_retrieval_rounds=int(
            os.getenv("ENDORAG_MAX_RETRIEVAL_ROUNDS", "2")
        ),
        retrieval_top_k=int(os.getenv("ENDORAG_RETRIEVAL_TOP_K", "5")),
        chroma_root=os.getenv("ENDORAG_CHROMA_ROOT", ""),
        collection_name=os.getenv("ENDORAG_COLLECTION_NAME", "quickstart"),
        source_dir=os.getenv("ENDORAG_DOCUMENT_ROOT", ""),
    )
