"""HuggingFace Qwen3-Reranker via sentence-transformers CrossEncoder."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from llama_index.core.bridge.pydantic import Field, PrivateAttr
from llama_index.core.postprocessor.types import BaseNodePostprocessor
from llama_index.core.schema import MetadataMode, NodeWithScore, QueryBundle

from .config import DEFAULT_RERANK_INSTRUCTION, resolve_rerank_device

logger = logging.getLogger(__name__)

_qwen_rerank_cache: Dict[Tuple[str, int, str, str], Any] = {}
_cross_encoder_cache: Dict[Tuple[str, str, str], Any] = {}

# https://huggingface.co/Qwen/Qwen3-Reranker-8B
DEFAULT_PROMPT_NAME = "task"


def get_cached_qwen_rerank(
    model: str,
    top_n: int,
    instruction: Optional[str],
) -> "Qwen3RerankPostprocessor":
    instr = (instruction or DEFAULT_RERANK_INSTRUCTION).strip()
    device = resolve_rerank_device()
    key = (model, top_n, instr, device)
    if key not in _qwen_rerank_cache:
        _qwen_rerank_cache[key] = Qwen3RerankPostprocessor(
            model=model,
            top_n=top_n,
            instruction=instr,
            device=device,
        )
    return _qwen_rerank_cache[key]


def _load_cross_encoder(model_id: str, instruction: str, device: str):
    try:
        from sentence_transformers import CrossEncoder
    except ImportError as exc:
        raise ImportError(
            "Qwen3-Reranker requires sentence-transformers. "
            "pip install 'sentence-transformers>=3.0.0'"
        ) from exc

    cache_key = (model_id, instruction, device)
    if cache_key in _cross_encoder_cache:
        return _cross_encoder_cache[cache_key]

    print(
        f"📥 Loading Qwen3 reranker {model_id} on {device} "
        f"(CrossEncoder, prompt={DEFAULT_PROMPT_NAME!r})...",
        flush=True,
    )
    encoder = CrossEncoder(
        model_id,
        device=device,
        prompts={DEFAULT_PROMPT_NAME: instruction},
        default_prompt_name=DEFAULT_PROMPT_NAME,
    )
    _cross_encoder_cache[cache_key] = encoder
    return encoder


class Qwen3RerankPostprocessor(BaseNodePostprocessor):
    """Rerank via Qwen/Qwen3-Reranker-* with the configured instruction."""

    model: str = Field(
        default="Qwen/Qwen3-Reranker-8B",
        description="Qwen3 reranker model id on HuggingFace.",
    )
    top_n: int = Field(description="Number of nodes to return sorted by score.")
    instruction: str = Field(
        default=DEFAULT_RERANK_INSTRUCTION,
        description="Task instruction passed as CrossEncoder prompt.",
    )
    _cross_encoder: Any = PrivateAttr(default=None)

    def __init__(
        self,
        model: str,
        top_n: int = 3,
        instruction: Optional[str] = None,
        device: Optional[str] = None,
    ) -> None:
        resolved_instruction = (
            instruction or DEFAULT_RERANK_INSTRUCTION
        ).strip()
        resolved_device = device or resolve_rerank_device()
        super().__init__(
            model=model,
            top_n=top_n,
            instruction=resolved_instruction,
        )
        self._cross_encoder = _load_cross_encoder(
            model, resolved_instruction, resolved_device
        )

    @classmethod
    def class_name(cls) -> str:
        return "Qwen3RerankPostprocessor"

    def _postprocess_nodes(
        self,
        nodes: List[NodeWithScore],
        query_bundle: Optional[QueryBundle] = None,
    ) -> List[NodeWithScore]:
        if query_bundle is None:
            raise ValueError("Missing query bundle for Qwen3 reranking.")
        if not nodes:
            return []

        query = query_bundle.query_str
        documents = [
            node.node.get_content(metadata_mode=MetadataMode.EMBED) for node in nodes
        ]
        pairs = [(query, doc) for doc in documents]

        try:
            raw_scores = self._cross_encoder.predict(pairs, batch_size=8)
        except Exception as exc:
            raise RuntimeError(
                f"Qwen3 rerank failed for model {self.model!r}: {exc}"
            ) from exc

        scores = [float(s) for s in raw_scores]
        for node, score in zip(nodes, scores):
            node.node.metadata["retrieval_score"] = node.score
            node.score = score

        return sorted(nodes, key=lambda x: -(x.score or 0.0))[: self.top_n]
