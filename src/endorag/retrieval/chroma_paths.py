"""Deterministic Chroma path construction."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any


def infer_chunk_db_segment(transformations: list[Any] | None) -> str | None:
    for item in transformations or []:
        if not item:
            continue
        tokens = [item] if isinstance(item, str) else list(item)
        class_name = str(tokens[0])
        payload: object = " ".join(str(token) for token in tokens[1:])
        if not payload and " " in class_name:
            class_name, payload = class_name.split(" ", 1)
        if isinstance(payload, str) and payload:
            try:
                payload = ast.literal_eval(payload)
            except (SyntaxError, ValueError):
                payload = {}
        if not isinstance(payload, dict):
            payload = {}
        if "SentenceSplitter" in class_name and "chunk_size" in payload:
            return f"chunk_{int(payload['chunk_size'])}"
        if "HybridChunker" in class_name and "max_tokens" in payload:
            return f"maxtokens_{int(payload['max_tokens'])}"
    return None


def auto_vector_db_path(
    root: str | Path,
    embed_model: str,
    category_slug: str,
    chunk_segment: str | None = None,
) -> Path:
    path = Path(root) / embed_model
    if chunk_segment:
        path /= chunk_segment
    return path / category_slug
