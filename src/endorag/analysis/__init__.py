"""Paper analysis utilities for EndoRAG evaluation artifacts."""

from endorag.analysis.artifact_resolver import ArtifactResolver, get_resolver
from endorag.analysis.paper_config import PaperAnalysisConfig, load_paper_config

__all__ = [
    "ArtifactResolver",
    "PaperAnalysisConfig",
    "get_resolver",
    "load_paper_config",
]
