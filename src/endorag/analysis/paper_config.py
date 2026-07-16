"""Load paper analysis manifest (configs/experiments/paper_analysis.yaml)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class PaperAnalysisConfig:
    repo_root: Path
    manifest_path: Path
    result_roots: dict[str, Path]
    manifests: dict[str, Path]
    routing_maps: dict[str, Path]

    @property
    def llm_root(self) -> Path:
        return self.result_roots["llm_only"]

    @property
    def vector_rag_root(self) -> Path:
        return self.result_roots["vector_rag_main"]

    @property
    def endorag_root(self) -> Path:
        return self.result_roots["endorag_main"]

    @property
    def oracle_root(self) -> Path:
        return self.result_roots["oracle_routing"]

    @property
    def literature_root(self) -> Path:
        return self.result_roots["literature_corpus"]


def load_paper_config(
    manifest_path: Path | str,
    repo_root: Path | str | None = None,
) -> PaperAnalysisConfig:
    manifest_path = Path(manifest_path).resolve()
    if repo_root is None:
        repo_root = manifest_path.parents[2]
    repo_root = Path(repo_root).resolve()

    with manifest_path.open(encoding="utf-8") as handle:
        payload: dict[str, Any] = yaml.safe_load(handle) or {}

    result_roots_raw = payload.get("result_roots") or {}
    result_roots = {
        key: (repo_root / str(value)).resolve()
        for key, value in result_roots_raw.items()
    }

    manifests_raw = payload.get("manifests") or {}
    manifests = {
        key: (repo_root / str(value)).resolve()
        for key, value in manifests_raw.items()
    }

    routing_maps = {
        "MCQ_question_category_map.json": repo_root / "data/routing/mcq_diabetes_category_map.json",
        "UKEU_question_category_map.json": repo_root / "data/routing/ukeu_category_map.json",
    }

    return PaperAnalysisConfig(
        repo_root=repo_root,
        manifest_path=manifest_path,
        result_roots=result_roots,
        manifests=manifests,
        routing_maps=routing_maps,
    )
