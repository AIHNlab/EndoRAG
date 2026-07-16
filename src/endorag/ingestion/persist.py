"""Vector index persistence helpers."""

from __future__ import annotations

from pathlib import Path

from llama_index.core import StorageContext, load_index_from_storage


def save_index(save_dir: str | Path, index: object) -> None:
    Path(save_dir).mkdir(parents=True, exist_ok=True)
    index.storage_context.persist(persist_dir=str(save_dir))


def load_index(load_dir: str | Path):
    storage_context = StorageContext.from_defaults(persist_dir=str(load_dir))
    return load_index_from_storage(storage_context)
