"""Rerank model routing and shared YAML configuration."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import yaml

logger = logging.getLogger(__name__)

DEFAULT_RERANK_CONFIG_PATH = "configs/rerank.yaml"
DEFAULT_QWEN3_RERANK_MODEL = "Qwen/Qwen3-Reranker-8B"
DEFAULT_RERANK_INSTRUCTION = (
    "Given a multiple-choice question in clinical endocrinology, "
    "judge whether the retrieved context provides information needed to "
    "determine the correct answer"
)
HF_CROSS_ENCODER_PREFIXES = ("cross-encoder/", "BAAI/", "sentence-transformers/")


def resolve_rerank_device() -> str:
    """Device for all rerank models (override with env ``RERANK_DEVICE``)."""
    override = os.getenv("RERANK_DEVICE", "").strip()
    if override:
        return override
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda:1"
    except ImportError:
        pass
    return "cpu"


def load_rerank_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    """Load rerank settings from YAML."""
    path = Path(
        config_path or os.getenv("RERANK_CONFIG", DEFAULT_RERANK_CONFIG_PATH)
    )
    defaults: Dict[str, Any] = {
        "instruction": os.getenv("RERANK_INSTRUCTION", DEFAULT_RERANK_INSTRUCTION),
        "model": os.getenv("QWEN3_RERANK_MODEL", DEFAULT_QWEN3_RERANK_MODEL),
    }
    if not path.is_file():
        return defaults
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Rerank config must be a JSON object: {path}")
    merged = {**defaults, **data}
    merged["instruction"] = str(
        merged.get("instruction", defaults["instruction"])
    ).strip()
    merged["model"] = str(merged.get("model", defaults["model"])).strip()
    return merged


def normalize_rerank_model_id(rerank_model: str) -> str:
    """Map legacy Ollama Qwen3 rerank ids to the HuggingFace hub id."""
    model = (rerank_model or "").strip()
    if not model:
        return DEFAULT_QWEN3_RERANK_MODEL
    if model.lower().startswith("ollama:"):
        model = model.split(":", 1)[1].strip()
    lower = model.lower()
    if "qwen3-reranker" in lower and ":" in model:
        logger.warning(
            "Ollama rerank is removed; using %s instead of %r",
            DEFAULT_QWEN3_RERANK_MODEL,
            rerank_model,
        )
        return DEFAULT_QWEN3_RERANK_MODEL
    if "/" in model and ":" in model:
        raise ValueError(
            f"Ollama-style rerank model ids are not supported ({rerank_model!r}). "
            f"Use a HuggingFace id such as {DEFAULT_QWEN3_RERANK_MODEL!r}."
        )
    return model


def is_qwen3_rerank_model(rerank_model: str) -> bool:
    """True for Qwen3-Reranker Hugging Face models."""
    model = normalize_rerank_model_id(rerank_model).lower()
    return "qwen3-reranker" in model or model.startswith("qwen/qwen3-reranker")


def is_jina_rerank_model(rerank_model: str) -> bool:
    """True if rerank_model is a Jina HF reranker (e.g. jinaai/jina-reranker-v3)."""
    model = rerank_model.strip().lower()
    return model.startswith("jinaai/") or "jina-reranker" in model


def resolve_rerank_config(rerank_model: str) -> Tuple[str, str]:
    """
    Pick backend from model id when --use-rerank is set.

    Returns:
        (backend, model_id) where backend is "qwen", "jina", or "cross_encoder".
    """
    model = normalize_rerank_model_id(rerank_model)
    if not model:
        raise ValueError("rerank_model must be set when use_rerank is enabled")

    if is_jina_rerank_model(model):
        return "jina", model
    if is_qwen3_rerank_model(model):
        return "qwen", model
    return "cross_encoder", model
