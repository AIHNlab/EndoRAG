"""Domain-specific vector database registry and routing."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from endorag.ingestion.index_builder import IndexBuilder

from .chroma_paths import auto_vector_db_path
from .query_engine import QueryEngineOptions, build_query_engine
from .routing import classify_endocrine_routing_category


def category_slug(category: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", category.lower().replace(",", "")).strip("_")


def embed_collection_prefix(embed_model: str) -> str:
    name = embed_model.split("/")[-1].split(":")[0]
    return re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")


@dataclass(frozen=True)
class VectorDbEntry:
    category: str
    source_dir: Path
    db_path: Path
    collection_name: str
    default: bool = False


class VectorDbRegistry:
    def __init__(
        self,
        entries: list[VectorDbEntry],
        *,
        transformations: list[Any],
        docling_config: Path,
        query_options: QueryEngineOptions,
        allow_build: bool = False,
    ) -> None:
        if not entries:
            raise ValueError("At least one vector database entry is required")
        self.entries = {entry.category: entry for entry in entries}
        self.default_category = next(
            (entry.category for entry in entries if entry.default),
            entries[0].category,
        )
        self.transformations = transformations
        self.docling_config = docling_config
        self.query_options = query_options
        self.allow_build = allow_build
        self.indexes: dict[str, Any] = {}
        self.engines: dict[str, Any] = {}

    @classmethod
    def from_manifest(
        cls,
        manifest_path: str | Path,
        *,
        embed_model: str,
        chroma_root: str | Path,
        chunk_segment: str | None,
        transformations: list[Any],
        docling_config: str | Path,
        query_options: QueryEngineOptions,
        allow_build: bool = False,
    ) -> "VectorDbRegistry":
        path = Path(manifest_path)
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        rows = payload.get("collections", []) if isinstance(payload, dict) else payload
        if not isinstance(rows, list) or not rows:
            raise ValueError(f"Corpus manifest must define non-empty collections: {path}")
        prefix = embed_collection_prefix(embed_model)
        entries: list[VectorDbEntry] = []
        for row in rows:
            category = str(row["category"])
            slug = category_slug(category)
            source_dir = Path(os.path.expandvars(str(row["source_dir"])))
            db_path = Path(
                os.path.expandvars(
                    str(
                        row.get(
                            "db_path",
                            auto_vector_db_path(
                                chroma_root,
                                embed_model,
                                slug,
                                chunk_segment,
                            ),
                        )
                    )
                )
            )
            entries.append(
                VectorDbEntry(
                    category=category,
                    source_dir=source_dir,
                    db_path=db_path,
                    collection_name=str(
                        row.get("collection_name", f"{prefix}_{slug}")
                    ),
                    default=bool(row.get("default", False)),
                )
            )
        return cls(
            entries,
            transformations=transformations,
            docling_config=Path(docling_config),
            query_options=query_options,
            allow_build=allow_build,
        )

    def build_all(self) -> dict[str, Any]:
        for category, entry in self.entries.items():
            builder = IndexBuilder(
                source_dir=entry.source_dir,
                db_path=entry.db_path,
                collection_name=entry.collection_name,
                transformations=self.transformations,
                docling_config=self.docling_config,
            )
            if not builder.collection_has_data() and not self.allow_build:
                raise FileNotFoundError(
                    f"Chroma collection {entry.collection_name!r} is empty at "
                    f"{entry.db_path}; run `endorag index build` first."
                )
            index = builder.build()
            self.indexes[category] = index
            self.engines[category] = build_query_engine(
                index,
                options=self.query_options,
            )
        return self.engines

    def resolve_category(
        self,
        question: str,
        *,
        llm: Any,
        pinned_category: str | None = None,
        oracle_map_path: str | Path | None = None,
    ) -> str:
        if len(self.entries) == 1:
            return next(iter(self.entries))
        if pinned_category in self.entries:
            return pinned_category
        if oracle_map_path:
            mapping = json.loads(Path(oracle_map_path).read_text(encoding="utf-8"))
            oracle_category = mapping.get(str(question).strip())
            if oracle_category in self.entries:
                return oracle_category
        category = classify_endocrine_routing_category(llm, question)
        return category if category in self.entries else self.default_category

    def route(
        self,
        question: str,
        *,
        llm: Any,
        pinned_category: str | None = None,
        oracle_map_path: str | Path | None = None,
    ) -> tuple[Any, str]:
        if not self.engines:
            self.build_all()
        category = self.resolve_category(
            question,
            llm=llm,
            pinned_category=pinned_category,
            oracle_map_path=oracle_map_path,
        )
        return self.engines[category], category
