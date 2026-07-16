"""Shared CLI arguments for paper analysis scripts."""

from __future__ import annotations

import argparse
from pathlib import Path

from endorag.analysis.artifact_resolver import configure


def add_paper_analysis_args(
    parser: argparse.ArgumentParser,
    *,
    default_manifest: str = "configs/experiments/paper_analysis.yaml",
    default_output_dir: str = "results/analysis_exports",
) -> None:
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="Repository root (default: inferred from --manifest).",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=default_manifest,
        help="Paper analysis manifest YAML.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=default_output_dir,
        help="Directory for analysis exports.",
    )


def resolve_analysis_context(args: argparse.Namespace) -> tuple[Path, Path, Path]:
    manifest = Path(args.manifest)
    if not manifest.is_absolute():
        repo_root = Path(args.repo_root) if args.repo_root else Path.cwd()
        manifest = (repo_root / manifest).resolve()
    repo_root = Path(args.repo_root).resolve() if args.repo_root else manifest.parents[2]
    output_dir = Path(args.output_dir)
    if not output_dir.is_absolute():
        output_dir = (repo_root / output_dir).resolve()
    configure(manifest, repo_root=repo_root)
    return repo_root, manifest, output_dir
