"""Validated experiment configuration."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator


def _expand(value: Any) -> Any:
    if isinstance(value, str):
        expanded = os.path.expandvars(value)
        if "${" in expanded:
            raise ValueError(f"Unresolved environment variable in {value!r}")
        return expanded
    if isinstance(value, list):
        return [_expand(item) for item in value]
    if isinstance(value, dict):
        return {key: _expand(item) for key, item in value.items()}
    return value


class ProviderManifest(BaseModel):
    llm_name: str
    embed_model_name: str | None = None
    llm_params: dict[str, Any] = Field(default_factory=dict)
    embed_params: dict[str, Any] = Field(default_factory=dict)


class RetrievalConfig(BaseModel):
    top_k: int = Field(default=5, ge=1)
    chunk_size: int = Field(default=512, ge=1)
    chunk_overlap: int = Field(default=100, ge=0)
    candidate_multiplier: float = Field(default=4.0, ge=1.0)
    use_rerank: bool = False
    rerank_model: str | None = None
    rerank_top_n: int = Field(default=5, ge=1)

    @model_validator(mode="after")
    def validate_reranker(self) -> "RetrievalConfig":
        if self.use_rerank and not self.rerank_model:
            raise ValueError("rerank_model is required when use_rerank is true")
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk_overlap must be smaller than chunk_size")
        return self


class ExperimentManifest(BaseModel):
    name: str
    method: Literal["llm", "vector-rag", "endorag"]
    dataset: Path
    output: Path
    log: Path
    provider: ProviderManifest
    retrieval: RetrievalConfig | None = None
    seed: int = 42
    corpus_manifest: Path | None = None
    oracle_routing: bool = False

    @model_validator(mode="after")
    def validate_method(self) -> "ExperimentManifest":
        if self.method == "llm" and self.retrieval is not None:
            raise ValueError("LLM-only experiments cannot define retrieval settings")
        if self.method != "llm" and self.retrieval is None:
            raise ValueError(f"{self.method} experiments require retrieval settings")
        return self

    @classmethod
    def _repo_root_for(cls, manifest_path: Path) -> Path:
        if len(manifest_path.parents) >= 3:
            return manifest_path.parents[2]
        return manifest_path.parent

    @classmethod
    def _resolve_paths(cls, manifest: "ExperimentManifest", base: Path) -> "ExperimentManifest":
        for field_name in ("dataset", "output", "log", "corpus_manifest"):
            value = getattr(manifest, field_name)
            if value is not None and not value.is_absolute():
                setattr(manifest, field_name, (base / value).resolve())
        return manifest

    @classmethod
    def from_mapping(cls, payload: dict[str, Any], *, base: Path) -> "ExperimentManifest":
        manifest = cls.model_validate(payload)
        return cls._resolve_paths(manifest, base)

    @classmethod
    def load(cls, path: str | Path) -> "ExperimentManifest":
        manifests = load_experiment_manifests(path)
        if len(manifests) != 1:
            raise ValueError(
                f"Expected a single experiment in {path}; found {len(manifests)}. "
                "Use load_experiment_manifests() for multi-experiment files."
            )
        return manifests[0]


def load_experiment_manifests(path: str | Path) -> list[ExperimentManifest]:
    """Load one or more experiment manifests from a YAML file."""
    manifest_path = Path(path).resolve()
    payload = _expand(yaml.safe_load(manifest_path.read_text(encoding="utf-8")))
    if not isinstance(payload, dict):
        raise ValueError(f"Experiment manifest must be a mapping: {manifest_path}")

    if "experiments" in payload:
        entries = payload["experiments"]
        if not isinstance(entries, list) or not entries:
            raise ValueError(f"'experiments' must be a non-empty list in {manifest_path}")
    else:
        entries = [payload]

    base = ExperimentManifest._repo_root_for(manifest_path)
    return [ExperimentManifest.from_mapping(entry, base=base) for entry in entries]
