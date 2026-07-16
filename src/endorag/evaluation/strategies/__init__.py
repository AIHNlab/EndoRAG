"""Lazy strategy loading after manifest validation."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from endorag.evaluation.manifest import ExperimentManifest
    from endorag.evaluation.models import EvaluationStrategy


def load_strategy(manifest: "ExperimentManifest", *, allow_build: bool = False) -> "EvaluationStrategy":
    from endorag.evaluation.runner import build_registry, build_vector_args
    from endorag.retrieval.routing import (
        infer_routing_category_from_dataset_path,
        question_category_map_path_for_dataset,
    )

    # Dataset labels are ground truth and must only influence live routing in
    # explicit oracle runs. Normal experiments classify every question with the
    # configured LLM, including questions from monotopic datasets.
    pinned_category = (
        infer_routing_category_from_dataset_path(str(manifest.dataset))
        if manifest.oracle_routing
        else None
    )
    oracle_map_path = (
        question_category_map_path_for_dataset(str(manifest.dataset))
        if manifest.oracle_routing
        else None
    )

    if manifest.method == "llm":
        from endorag.evaluation.strategies.llm import LLMStrategy

        return LLMStrategy()

    if manifest.method == "vector-rag":
        from endorag.evaluation.strategies.vector_rag import VectorRAGStrategy

        registry = build_registry(manifest, allow_build=allow_build)
        return VectorRAGStrategy(
            registry,
            pinned_category=pinned_category,
            oracle_map_path=oracle_map_path,
        )

    if manifest.method == "endorag":
        from endorag.evaluation.strategies.endorag import (
            EndoRAGStrategy,
            build_endorag_deps,
        )
        from endorag.retrieval.vector_tools import VectorTools

        registry = build_registry(manifest, allow_build=allow_build)
        vector_args = build_vector_args(manifest)
        vector_tools = VectorTools.from_args(vector_args)
        deps = build_endorag_deps(
            vector_tools,
            llm_name=manifest.provider.llm_name,
            seed=manifest.seed,
        )
        return EndoRAGStrategy(
            deps,
            routing_registry=registry,
            pinned_category=pinned_category,
            oracle_map_path=oracle_map_path,
        )

    raise ValueError(f"Unsupported evaluation method: {manifest.method}")
