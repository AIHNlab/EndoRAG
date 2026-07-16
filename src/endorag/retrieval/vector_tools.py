from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from llama_index.core import Settings

from endorag.ingestion.index_builder import IndexBuilder
from endorag.providers.settings import get_settings
from endorag.retrieval.chroma_paths import auto_vector_db_path, infer_chunk_db_segment
from endorag.retrieval.evidence_models import EvidencePassage
from endorag.retrieval.query_engine import QueryEngineOptions, build_query_engine


_VECTOR_RETRIEVAL_DEFAULTS: dict[str, Any] = {
    "doc_transformations": [
        [
            "llama_index.core.node_parser.SentenceSplitter",
            "{'chunk_size': 512, 'chunk_overlap': 100}",
        ],
    ],
    "use_rerank": True,
    "rerank_top_n": 5,
    "rerank_candidate_multiplier": 4.0,
    "rerank_model": "Qwen/Qwen3-Reranker-8B",
    "rerank_config": "configs/rerank.yaml",
}


@dataclass(frozen=True)
class VectorDomainConfig:
    slug: str
    db_path: str
    collection_name: str
    source_dir: str
    top_k: int


class VectorTools:
    """Thin retrieval wrapper that can reuse an existing query engine or KnowledgeBase."""

    def __init__(
        self,
        query_engine: Any | None = None,
        domain_configs: dict[str, VectorDomainConfig] | None = None,
        args: Any | None = None,
        llm: Any | None = None,
        embed_model: Any | None = None,
    ) -> None:
        self.settings = get_settings()
        self.query_engine = query_engine
        self.domain_configs = domain_configs or self._load_domain_configs(args)
        self.args = args
        self.llm = llm or Settings.llm
        self.embed_model = embed_model or Settings.embed_model
        self._query_engines: dict[str, Any] = {}

    @classmethod
    def from_args(
        cls,
        args: Any,
        llm: Any | None = None,
        embed_model: Any | None = None,
    ) -> "VectorTools":
        return cls(args=args, llm=llm, embed_model=embed_model)

    async def retrieve(
        self,
        query: str,
        domain: str | None = None,
        top_k: int | None = None,
        retrieval_round: int = 1,
    ) -> tuple[list[EvidencePassage], dict[str, Any]]:
        config = self._resolve_domain(domain)
        retrieval_settings = _retrieval_settings(self.args)
        start = time.perf_counter()
        try:
            query_engine = self.query_engine or self._query_engine_for_domain(config, top_k)
            response = query_engine.query(query)
            passages = _passages_from_response(
                response=response,
                query=query,
                domain=config.slug,
                retrieval_round=retrieval_round,
            )
            provenance = {
                "tool": "endorag_vector_retrieve",
                "args": _provenance_retrieval_args(
                    query=query,
                    config=config,
                    top_k=top_k,
                    retrieval_settings=retrieval_settings,
                ),
                "success": True,
                "latency_ms": (time.perf_counter() - start) * 1000,
                "result_count": len(passages),
                "passages": [
                    {
                        "id": passage.id,
                        "source": passage.source,
                        "score": passage.score,
                    }
                    for passage in passages
                ],
            }
            return passages, provenance
        except Exception as exc:  # noqa: BLE001
            provenance = {
                "tool": "endorag_vector_retrieve",
                "args": _provenance_retrieval_args(
                    query=query,
                    config=config,
                    top_k=top_k,
                    retrieval_settings=retrieval_settings,
                ),
                "success": False,
                "latency_ms": (time.perf_counter() - start) * 1000,
                "error": repr(exc),
            }
            return [], provenance

    def _query_engine_for_domain(self, config: VectorDomainConfig, top_k: int | None) -> Any:
        retrieval_settings = _retrieval_settings(self.args)
        effective_top_k = top_k or config.top_k
        rerank_top_n = retrieval_settings["rerank_top_n"] or effective_top_k
        cache_key = (
            f"{config.slug}:{effective_top_k}:rerank={retrieval_settings['use_rerank']}:"
            f"{rerank_top_n}:{retrieval_settings['rerank_model']}"
        )
        if cache_key in self._query_engines:
            return self._query_engines[cache_key]

        args = self.args
        builder = IndexBuilder(
            source_dir=config.source_dir,
            db_path=config.db_path,
            collection_name=config.collection_name,
            transformations=retrieval_settings["doc_transformations"],
            docling_config=getattr(args, "docling_config", "configs/docling.yaml"),
        )
        if not builder.collection_has_data():
            raise FileNotFoundError(
                f"Chroma collection {config.collection_name!r} is empty at "
                f"{config.db_path}; run `endorag index build` first."
            )
        index = builder.build()
        query_engine = build_query_engine(
            index,
            options=QueryEngineOptions(
                top_k=effective_top_k,
                use_rerank=retrieval_settings["use_rerank"],
                rerank_top_n=rerank_top_n,
                rerank_model=retrieval_settings["rerank_model"],
                rerank_config=retrieval_settings["rerank_config"],
                candidate_multiplier=retrieval_settings["rerank_candidate_multiplier"],
                retrieve_only=True,
            ),
        )
        self._query_engines[cache_key] = query_engine
        return query_engine

    def _resolve_domain(self, domain: str | None) -> VectorDomainConfig:
        if domain and domain in self.domain_configs:
            return self.domain_configs[domain]
        if domain:
            normalized = _category_to_slug(domain)
            if normalized in self.domain_configs:
                return self.domain_configs[normalized]
        return self.domain_configs.get("default") or next(iter(self.domain_configs.values()))

    def _load_domain_configs(self, args: Any | None) -> dict[str, VectorDomainConfig]:
        top_k = int(getattr(args, "top_k", self.settings.retrieval_top_k) or self.settings.retrieval_top_k)
        db_configs = self._load_db_config_domain_configs(args, top_k)
        if db_configs:
            return db_configs

        db_path = getattr(args, "db_path", self.settings.chroma_root)
        collection_name = getattr(args, "collection_name", self.settings.collection_name)
        source_dir = getattr(args, "dir", self.settings.source_dir)
        default_config = VectorDomainConfig(
            slug="default",
            db_path=db_path,
            collection_name=collection_name,
            source_dir=source_dir,
            top_k=top_k,
        )
        return {
            "default": default_config,
        }

    def _load_db_config_domain_configs(
        self,
        args: Any | None,
        top_k: int,
    ) -> dict[str, VectorDomainConfig] | None:
        config_path = getattr(args, "db_config", None)
        if not config_path:
            return None
        with open(config_path, "r", encoding="utf-8") as f:
            db_configs = yaml.safe_load(f)
        if isinstance(db_configs, dict):
            db_configs = db_configs.get("collections", [])
        if not isinstance(db_configs, list) or not db_configs:
            return None

        embed_model = getattr(args, "embed_model_name", "")
        collection_prefix = _embed_model_to_collection(embed_model)
        chunk_segment = infer_chunk_db_segment(
            getattr(args, "doc_transformations", None)
        )
        chroma_root = getattr(args, "chroma_root", self.settings.chroma_root)
        domain_configs: dict[str, VectorDomainConfig] = {}
        default_key: str | None = None

        for idx, cfg in enumerate(db_configs):
            category = str(cfg.get("category") or f"db_{idx + 1}")
            slug = _category_to_slug(category)
            db_path = cfg.get("db_path") or auto_vector_db_path(
                chroma_root,
                embed_model,
                slug,
                chunk_segment,
            )
            collection_name = cfg.get("collection_name") or f"{collection_prefix}_{slug}"
            source_dir = cfg.get(
                "source_dir",
                cfg.get("dir", getattr(args, "dir", self.settings.source_dir)),
            )
            domain_config = VectorDomainConfig(
                slug=category,
                db_path=db_path,
                collection_name=collection_name,
                source_dir=source_dir,
                top_k=top_k,
            )
            # Support both the human category label and filesystem slug.
            domain_configs[category] = domain_config
            domain_configs[slug] = domain_config
            if cfg.get("default", False) or default_key is None:
                default_key = category

        if default_key:
            domain_configs["default"] = domain_configs[default_key]
        return domain_configs


def _retrieval_settings(args: Any | None) -> dict[str, Any]:
    """Effective vector retrieval settings (defaults aligned with run_exam_eval_flow.sh)."""
    settings = dict(_VECTOR_RETRIEVAL_DEFAULTS)
    if args is None:
        return settings

    if getattr(args, "doc_transformations", None):
        settings["doc_transformations"] = args.doc_transformations
    if hasattr(args, "use_rerank"):
        settings["use_rerank"] = bool(args.use_rerank)
    if getattr(args, "rerank_top_n", None) is not None:
        settings["rerank_top_n"] = args.rerank_top_n
    if getattr(args, "rerank_candidate_multiplier", None) is not None:
        settings["rerank_candidate_multiplier"] = args.rerank_candidate_multiplier
    if getattr(args, "rerank_model", None):
        settings["rerank_model"] = args.rerank_model
    if getattr(args, "rerank_config", None):
        settings["rerank_config"] = args.rerank_config
    return settings


def _provenance_retrieval_args(
    *,
    query: str,
    config: VectorDomainConfig,
    top_k: int | None,
    retrieval_settings: dict[str, Any],
) -> dict[str, Any]:
    effective_top_k = top_k or config.top_k
    rerank_top_n = retrieval_settings.get("rerank_top_n") or effective_top_k
    return {
        "query": query,
        "domain": config.slug,
        "top_k": effective_top_k,
        "db_path": str(config.db_path),
        "collection_name": config.collection_name,
        "doc_transformations": retrieval_settings["doc_transformations"],
        "use_rerank": retrieval_settings["use_rerank"],
        "rerank_top_n": rerank_top_n,
        "rerank_candidate_multiplier": retrieval_settings["rerank_candidate_multiplier"],
        "rerank_model": retrieval_settings["rerank_model"],
        "rerank_config": str(retrieval_settings["rerank_config"]),
    }


def _passages_from_response(
    response: Any,
    query: str,
    domain: str,
    retrieval_round: int,
) -> list[EvidencePassage]:
    passages: list[EvidencePassage] = []
    source_nodes = getattr(response, "source_nodes", []) or []
    for idx, node_with_score in enumerate(source_nodes):
        node = getattr(node_with_score, "node", node_with_score)
        metadata = {
            key: str(value) if isinstance(value, Path) else value
            for key, value in dict(getattr(node, "metadata", {}) or {}).items()
        }
        text = getattr(node, "text", None)
        if text is None and hasattr(node, "get_content"):
            text = node.get_content()
        text = str(text or "").strip()
        if not text:
            continue
        source = metadata.get("file_name") or metadata.get("file_path") or metadata.get("source") or "unknown"
        passages.append(
            EvidencePassage(
                id=_passage_id(str(source), text, idx),
                text=text,
                source=str(source),
                score=_json_safe_score(getattr(node_with_score, "score", None)),
                domain=domain,
                query=query,
                retrieval_round=retrieval_round,
                metadata=metadata,
            )
        )
    return passages


def _passage_id(source: str, text: str, idx: int) -> str:
    digest = hashlib.sha1(f"{source}:{text[:500]}:{idx}".encode("utf-8")).hexdigest()[:12]
    return f"ev_{digest}"


def _json_safe_score(score: Any) -> float | None:
    return None if score is None else float(score)


def _category_to_slug(category: str) -> str:
    slug = category.lower()
    slug = re.sub(r"[,]+", "", slug)
    slug = re.sub(r"[^a-z0-9]+", "_", slug)
    return slug.strip("_")


def _embed_model_to_collection(embed_model_name: str) -> str:
    name = str(embed_model_name).split("/")[-1]
    name = name.split(":")[0]
    name = re.sub(r"[^a-zA-Z0-9]+", "_", name)
    return name.strip("_")
