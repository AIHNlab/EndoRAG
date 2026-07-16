"""Cached sentence-transformer cross-encoder reranking."""

from __future__ import annotations

from typing import Any, ClassVar

from llama_index.core.bridge.pydantic import Field, PrivateAttr
from llama_index.core.postprocessor.types import BaseNodePostprocessor
from llama_index.core.schema import MetadataMode, NodeWithScore, QueryBundle

from .config import resolve_rerank_device


class CrossEncoderRerank(BaseNodePostprocessor):
    model: str = Field(description="Hugging Face cross-encoder identifier")
    top_n: int = Field(default=5, ge=1)
    device: str = Field(default="cpu")
    _encoder: Any = PrivateAttr()
    _cache: ClassVar[dict[tuple[str, str], Any]] = {}

    def __init__(self, *, model: str, top_n: int, device: str | None = None) -> None:
        resolved_device = device or resolve_rerank_device()
        super().__init__(model=model, top_n=top_n, device=resolved_device)
        key = (model, resolved_device)
        if key not in self._cache:
            from sentence_transformers import CrossEncoder

            self._cache[key] = CrossEncoder(model, device=resolved_device)
        self._encoder = self._cache[key]

    def _postprocess_nodes(
        self,
        nodes: list[NodeWithScore],
        query_bundle: QueryBundle | None = None,
    ) -> list[NodeWithScore]:
        if not nodes or query_bundle is None:
            return nodes[: self.top_n]
        pairs = [
            (
                query_bundle.query_str,
                node.node.get_content(metadata_mode=MetadataMode.NONE),
            )
            for node in nodes
        ]
        scores = self._encoder.predict(pairs)
        for node, score in zip(nodes, scores):
            node.score = float(score)
        return sorted(nodes, key=lambda item: item.score or float("-inf"), reverse=True)[
            : self.top_n
        ]


def get_cached_cross_encoder_rerank(
    model: str, top_n: int
) -> CrossEncoderRerank:
    return CrossEncoderRerank(model=model, top_n=top_n)
