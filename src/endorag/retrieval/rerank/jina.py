"""HuggingFace jina-reranker-v3 listwise reranker for vector retrieval."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from llama_index.core.bridge.pydantic import Field, PrivateAttr
from llama_index.core.postprocessor.types import BaseNodePostprocessor
from llama_index.core.schema import MetadataMode, NodeWithScore, QueryBundle

from .config import resolve_rerank_device

logger = logging.getLogger(__name__)

_jina_rerank_cache: Dict[Tuple[str, int, str], Any] = {}


def get_cached_jina_rerank(model: str, top_n: int) -> "JinaRerankPostprocessor":
    device = resolve_rerank_device()
    key = (model, top_n, device)
    if key not in _jina_rerank_cache:
        _jina_rerank_cache[key] = JinaRerankPostprocessor(
            model=model, top_n=top_n, device=device
        )
    return _jina_rerank_cache[key]


class JinaRerankPostprocessor(BaseNodePostprocessor):
    """Rerank via jinaai/jina-reranker-v3 (transformers, trust_remote_code)."""

    model: str = Field(
        default="jinaai/jina-reranker-v3",
        description="Jina reranker model id on HuggingFace.",
    )
    top_n: int = Field(description="Number of nodes to return sorted by score.")
    device: str = Field(description="Torch device (cuda or cpu).")
    _hf_model: Any = PrivateAttr(default=None)

    def __init__(
        self,
        model: str,
        top_n: int = 3,
        device: Optional[str] = None,
    ) -> None:
        resolved_device = device or resolve_rerank_device()
        super().__init__(model=model, top_n=top_n, device=resolved_device)
        try:
            from transformers import AutoModel
        except ImportError as exc:
            raise ImportError(
                "jina-reranker-v3 requires transformers. pip install transformers"
            ) from exc

        print(
            f"📥 Loading Jina reranker {model} on {resolved_device}...",
            flush=True,
        )
        hf_model = AutoModel.from_pretrained(
            model,
            trust_remote_code=True,
        )
        hf_model.eval()
        hf_model.to(resolved_device)
        self._hf_model = hf_model

    @classmethod
    def class_name(cls) -> str:
        return "JinaRerankPostprocessor"

    def _postprocess_nodes(
        self,
        nodes: List[NodeWithScore],
        query_bundle: Optional[QueryBundle] = None,
    ) -> List[NodeWithScore]:
        if query_bundle is None:
            raise ValueError("Missing query bundle for Jina reranking.")
        if not nodes:
            return []

        query = query_bundle.query_str
        documents = [
            node.node.get_content(metadata_mode=MetadataMode.EMBED) for node in nodes
        ]

        try:
            results = self._hf_model.rerank(query, documents, top_n=self.top_n)
        except Exception as exc:
            raise RuntimeError(
                f"Jina rerank failed for model {self.model!r}: {exc}"
            ) from exc

        ranked: List[NodeWithScore] = []
        for item in results:
            idx = int(item["index"])
            if idx < 0 or idx >= len(nodes):
                continue
            node = nodes[idx]
            node.node.metadata["retrieval_score"] = node.score
            node.score = float(item.get("relevance_score", 0.0))
            ranked.append(node)

        return ranked
