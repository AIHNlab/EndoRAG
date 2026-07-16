"""Build or load one persistent Chroma vector collection."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

import chromadb
from llama_index.core import Settings, StorageContext, VectorStoreIndex
from llama_index.core.ingestion import IngestionPipeline
from llama_index.vector_stores.chroma import ChromaVectorStore

from .docling import DoclingProcessor
from .transformations import get_transformations


class IndexBuilder:
    def __init__(
        self,
        *,
        source_dir: str | Path,
        db_path: str | Path,
        collection_name: str,
        transformations: Sequence[Any],
        docling_config: str | Path,
        cache_dir: str | Path = "markdown_documents",
    ) -> None:
        self.source_dir = Path(source_dir)
        self.db_path = Path(db_path)
        self.collection_name = collection_name
        self.transformation_specs = list(transformations)
        self.docling_config = Path(docling_config)
        self.cache_dir = Path(cache_dir)

    def _collection(self):
        self.db_path.mkdir(parents=True, exist_ok=True)
        client = chromadb.PersistentClient(path=str(self.db_path))
        return client.get_or_create_collection(
            self.collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    def collection_has_data(self) -> bool:
        return self._collection().count() > 0

    def build(self) -> VectorStoreIndex:
        collection = self._collection()
        store = ChromaVectorStore(chroma_collection=collection)
        if collection.count() > 0:
            return VectorStoreIndex.from_vector_store(store)
        if not self.source_dir.is_dir():
            raise FileNotFoundError(
                f"Source directory is required to build an empty collection: {self.source_dir}"
            )
        if not self.docling_config.is_file():
            raise FileNotFoundError(f"Docling config not found: {self.docling_config}")

        def class_name(specification: Any) -> str:
            return str(
                specification
                if isinstance(specification, str)
                else specification[0]
            )

        docling_specs = [
            specification
            for specification in self.transformation_specs
            if "docling_core" in class_name(specification)
        ]
        llama_specs = [
            specification
            for specification in self.transformation_specs
            if "docling_core" not in class_name(specification)
        ]
        processor = DoclingProcessor(
            config_path=self.docling_config,
            docling_transformations=docling_specs or None,
            cache_dir=self.cache_dir,
        )
        documents, nodes = processor.process_directory(
            source_dir=str(self.source_dir),
            file_extensions=[".pdf", ".docx", ".doc"],
        )
        if not nodes:
            pipeline = IngestionPipeline(
                transformations=get_transformations(llama_specs)
            )
            nodes = pipeline.run(
                documents=documents,
                in_place=True,
                show_progress=True,
            )
        if not nodes:
            raise ValueError(f"No nodes were produced from {self.source_dir}")
        context = StorageContext.from_defaults(vector_store=store)
        return VectorStoreIndex(
            nodes,
            storage_context=context,
            embed_model=Settings.embed_model,
        )
