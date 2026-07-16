"""Resolve evaluation JSON paths with legacy directory/name aliases."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from endorag.analysis.paper_config import PaperAnalysisConfig, load_paper_config

METHOD_DIR_ALIASES: dict[str, str] = {
    "Method_vectorRag": "Method_vectorRAG",
}

AGENTIC_SUBDIR_ALIASES: tuple[str, ...] = (
    "agentic_workflow_8B",
    "endorag_qwen8b",
    "agentic_workflow",
    "endorag",
)

EMBED_DIR = "qwen3-embedding:8b"
VECTOR_SUBDIR = "LLM/Cosine_C512_100"
RERANK_PREFIX = "rerank_qwen8b_Qwen_Qwen3-Reranker-8B_"

_RESOLVER: "ArtifactResolver | None" = None


def _slug_name(name: str) -> str:
    return name.replace("/", "_").replace(":", "_")


class ArtifactResolver:
    """Locate paper evaluation JSONs under Task 8 result roots."""

    def __init__(self, config: PaperAnalysisConfig) -> None:
        self.config = config
        self.repo_root = config.repo_root
        self.llm_root = config.llm_root
        self.vector_root = config.vector_rag_root
        self.endorag_root = config.endorag_root
        self.oracle_root = config.oracle_root
        self.literature_root = config.literature_root

    def normalize_method_segment(self, segment: str) -> str:
        return METHOD_DIR_ALIASES.get(segment, segment)

    def vector_llm_base(self, llm_dir: str, *, literature: bool = False) -> Path:
        """LLM directory under vector RAG results (embedding models are siblings below)."""
        root = self.literature_root if literature else self.vector_root
        return root / llm_dir

    def vector_embed_base(
        self, llm_dir: str, embed_dir: str, *, literature: bool = False
    ) -> Path:
        """One embedding model's directory: ``{vector_root}/{llm}/{embed_dir}``."""
        return self.vector_llm_base(llm_dir, literature=literature) / embed_dir

    def vector_base(self, llm_dir: str, *, literature: bool = False) -> Path:
        root = self.literature_root if literature else self.vector_root
        return root / llm_dir / EMBED_DIR

    def embed_suffixes(self) -> tuple[str, ...]:
        slug = _slug_name(EMBED_DIR)
        return (f"_{EMBED_DIR}_1.json", f"_{slug}_1.json")

    def find_vector_rag_file(
        self,
        llm_dir: str,
        vector_prefixes: tuple[str, ...],
        *,
        with_rerank: bool,
        rerank_prefix: str = RERANK_PREFIX,
        literature: bool = False,
        oracle: bool = False,
    ) -> Path | None:
        if oracle:
            base = self.oracle_root / llm_dir / EMBED_DIR / VECTOR_SUBDIR
        else:
            base = self.vector_base(llm_dir, literature=literature) / VECTOR_SUBDIR
        if not base.is_dir():
            return None

        suffixes = self.embed_suffixes()
        matches: list[Path] = []
        for path in base.iterdir():
            name = path.name
            if not name.endswith("_1.json"):
                continue
            if "diabetesVectorTool512_100" not in name:
                continue
            if with_rerank:
                if not name.startswith(rerank_prefix):
                    continue
            elif name.startswith("rerank_"):
                continue
            if not any(name.endswith(suffix) for suffix in suffixes):
                continue
            stem_after_rerank = name[len(rerank_prefix) :] if with_rerank else name
            if not any(stem_after_rerank.startswith(prefix) for prefix in vector_prefixes):
                continue
            matches.append(path)

        if not matches:
            return None
        matches.sort(key=lambda candidate: candidate.name)
        return matches[0]

    def find_llm_only_file(self, llm_dir: str, llm_slug: str) -> Path | None:
        base = self.llm_root / llm_dir / "LLM"
        if not base.is_dir():
            return None

        matches: list[Path] = []
        for path in base.iterdir():
            name = path.name
            if not name.endswith("_1.json"):
                continue
            if name.startswith(f"{llm_slug}_LLM_"):
                matches.append(path)

        if not matches:
            alt = llm_slug.replace("MCQs_book", "MCQs")
            for path in base.iterdir():
                name = path.name
                if name.endswith("_1.json") and name.startswith(f"{alt}_LLM_"):
                    matches.append(path)
        if not matches:
            return None
        matches.sort(key=lambda candidate: candidate.name)
        return matches[0]

    def find_agentic_file(self, llm_dir: str, agentic_slug: str) -> Path | None:
        candidates = (
            f"agentic_workflow_eval_{agentic_slug}.json",
            f"agentic_workflow_eval_{agentic_slug}_dataset.json",
            f"endorag_eval_{agentic_slug}.json",
            f"endorag_eval_{agentic_slug}_dataset.json",
        )
        search_roots = (
            self.vector_root / llm_dir / EMBED_DIR,
            self.endorag_root / llm_dir / EMBED_DIR,
        )
        for root in search_roots:
            if not root.is_dir():
                continue
            for subdir in AGENTIC_SUBDIR_ALIASES:
                base = root / subdir
                if not base.is_dir():
                    continue
                for name in candidates:
                    path = base / name
                    if path.is_file():
                        return path
        return None

    def load_oracle_map(self, filename: str | None) -> dict[str, Any]:
        if not filename:
            return {}
        path = self.config.routing_maps.get(filename)
        if path is None or not path.is_file():
            legacy = self.repo_root / "evaluate" / filename
            if legacy.is_file():
                path = legacy
            else:
                return {}
        import json

        with path.open(encoding="utf-8") as handle:
            return json.load(handle)

    def relative_to_repo(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.repo_root))
        except ValueError:
            return str(path)


def configure(
    manifest_path: Path | str,
    repo_root: Path | str | None = None,
) -> ArtifactResolver:
    global _RESOLVER
    config = load_paper_config(manifest_path, repo_root=repo_root)
    _RESOLVER = ArtifactResolver(config)
    return _RESOLVER


def get_resolver() -> ArtifactResolver:
    if _RESOLVER is None:
        raise RuntimeError("Artifact resolver not configured; pass --manifest first.")
    return _RESOLVER
