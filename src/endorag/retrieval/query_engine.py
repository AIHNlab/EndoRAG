"""Compose the paper's cosine retrieval and reranking pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from llama_index.core import VectorStoreIndex, get_response_synthesizer
from llama_index.core.indices.postprocessor import SimilarityPostprocessor
from llama_index.core.query_engine import RetrieverQueryEngine
from llama_index.core.response_synthesizers import ResponseMode
from llama_index.core.retrievers import VectorIndexRetriever

from .rerank.config import load_rerank_config, resolve_rerank_config
from .rerank.cross_encoder import get_cached_cross_encoder_rerank
from .rerank.jina import get_cached_jina_rerank
from .rerank.qwen import get_cached_qwen_rerank


@dataclass(frozen=True)
class QueryEngineOptions:
    top_k: int = 5
    use_rerank: bool = False
    rerank_top_n: int = 5
    rerank_model: str = "Qwen/Qwen3-Reranker-8B"
    rerank_config: str | Path | None = None
    candidate_multiplier: float = 4.0
    similarity_cutoff: float = 0.5
    retrieve_only: bool = False


def build_query_engine(
    index: VectorStoreIndex,
    *,
    options: QueryEngineOptions,
) -> RetrieverQueryEngine:
    if options.top_k < 1:
        raise ValueError("top_k must be positive")
    prefetch_k = options.top_k
    if options.use_rerank:
        prefetch_k = max(
            options.top_k,
            int(options.top_k * options.candidate_multiplier),
        )
    retriever = VectorIndexRetriever(
        index=index,
        similarity_top_k=prefetch_k,
    )
    response_synthesizer = get_response_synthesizer(
        response_mode=ResponseMode.NO_TEXT
    ) if options.retrieve_only else get_response_synthesizer()

    postprocessors: list[object] = []
    if options.use_rerank:
        backend, model = resolve_rerank_config(options.rerank_model)
        if backend == "qwen":
            rerank_config = load_rerank_config(
                str(options.rerank_config) if options.rerank_config else None
            )
            postprocessors.append(
                get_cached_qwen_rerank(
                    model=model,
                    top_n=options.rerank_top_n,
                    instruction=rerank_config["instruction"],
                )
            )
        elif backend == "jina":
            postprocessors.append(
                get_cached_jina_rerank(
                    model=model,
                    top_n=options.rerank_top_n,
                )
            )
        else:
            postprocessors.append(
                get_cached_cross_encoder_rerank(
                    model=model,
                    top_n=options.rerank_top_n,
                )
            )
    else:
        postprocessors.append(
            SimilarityPostprocessor(similarity_cutoff=options.similarity_cutoff)
        )

    return RetrieverQueryEngine.from_args(
        retriever=retriever,
        response_synthesizer=response_synthesizer,
        node_postprocessors=postprocessors,
    )
