"""EndoRAG command-line interface."""

from __future__ import annotations

import argparse
import os
import sys

os.environ.setdefault("OTEL_SDK_DISABLED", "true")

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(line_buffering=True)
        except (AttributeError, OSError, ValueError):
            pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="endorag",
        description="EndoRAG indexing, evaluation, and environment utilities.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    index_parser = subparsers.add_parser("index", help="Vector index operations.")
    index_sub = index_parser.add_subparsers(dest="index_command", required=True)
    index_build = index_sub.add_parser("build", help="Build Chroma collections from a manifest.")
    index_build.add_argument("--manifest", required=True, help="Experiment or corpus manifest path.")
    index_build.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate manifest and print planned index builds without writing data.",
    )

    evaluate_parser = subparsers.add_parser("evaluate", help="Run a manifest-driven evaluation.")
    evaluate_parser.add_argument("--manifest", required=True, help="Experiment manifest path.")
    evaluate_parser.add_argument("--limit", type=int, default=None, help="Evaluate only the first N items.")
    evaluate_parser.add_argument(
        "--resume",
        action="store_true",
        help="Continue from partial results in the manifest output path.",
    )
    evaluate_parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace complete results at the manifest output path.",
    )
    evaluate_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate manifest and print the selected strategy without running models.",
    )

    analyze_parser = subparsers.add_parser("analyze", help="Analyze saved evaluation results.")
    analyze_parser.add_argument("--manifest", required=True, help="Experiment manifest path.")

    environment_parser = subparsers.add_parser("environment", help="Environment reporting.")
    environment_sub = environment_parser.add_subparsers(dest="environment_command", required=True)
    environment_report = environment_sub.add_parser(
        "report",
        help="Write a JSON environment report.",
    )
    environment_report.add_argument(
        "--output",
        required=True,
        help="Destination path for the environment report JSON.",
    )
    return parser


def _print_index_dry_run(registry: object) -> None:
    for category, entry in registry.entries.items():
        print(
            f"[dry-run] {category}: source={entry.source_dir} "
            f"db={entry.db_path} collection={entry.collection_name}"
        )


def _build_registry_from_corpus_manifest(
    manifest_path: str,
    *,
    allow_build: bool,
) -> object:
    from pathlib import Path

    import yaml

    from endorag.retrieval.chroma_paths import infer_chunk_db_segment
    from endorag.retrieval.query_engine import QueryEngineOptions
    from endorag.retrieval.registry import VectorDbRegistry

    payload = yaml.safe_load(Path(manifest_path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or "collections" not in payload:
        raise ValueError(
            f"Corpus manifest must define 'collections': {manifest_path}"
        )

    chunk_size = 512
    chunk_overlap = 100
    transformations = [
        [
            "llama_index.core.node_parser.SentenceSplitter",
            f"{{'chunk_size': {chunk_size}, 'chunk_overlap': {chunk_overlap}}}",
        ]
    ]
    query_options = QueryEngineOptions(
        top_k=5,
        use_rerank=False,
        rerank_top_n=5,
        rerank_model="Qwen/Qwen3-Reranker-8B",
        rerank_config=os.getenv("ENDORAG_RERANK_CONFIG", "configs/rerank.yaml"),
        candidate_multiplier=4.0,
    )
    return VectorDbRegistry.from_manifest(
        manifest_path,
        embed_model="qwen3-embedding:8b",
        chroma_root=os.getenv("ENDORAG_CHROMA_ROOT", ""),
        chunk_segment=infer_chunk_db_segment(transformations),
        transformations=transformations,
        docling_config=os.getenv("ENDORAG_DOCLING_CONFIG", "configs/docling.yaml"),
        query_options=query_options,
        allow_build=allow_build,
    )


def _bootstrap_corpus_embedding_model() -> None:
    """Initialize the embedding model used by standalone corpus manifests."""
    from endorag.providers.models import ModelProvider, ProviderConfig

    ModelProvider(
        ProviderConfig(
            llm_name=os.getenv("ENDORAG_OLLAMA_MODEL", "mistral-nemo:latest"),
            embed_model_name="qwen3-embedding:8b",
        )
    ).load_embed_model()


def cmd_index_build(args: argparse.Namespace) -> int:
    from pathlib import Path

    import yaml

    from endorag.evaluation.manifest import ExperimentManifest
    from endorag.evaluation.runner import build_registry
    from endorag.providers.models import ModelProvider, ProviderConfig

    manifest_path = Path(args.manifest)
    payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and "collections" in payload:
        registry = _build_registry_from_corpus_manifest(
            args.manifest,
            allow_build=not args.dry_run,
        )
        if args.dry_run:
            _print_index_dry_run(registry)
            return 0

        _bootstrap_corpus_embedding_model()
        registry.build_all()
        print(f"Built {len(registry.entries)} vector index(es).")
        return 0

    manifest = ExperimentManifest.load(args.manifest)
    if manifest.method == "llm":
        raise ValueError("LLM-only manifests do not define vector indexes to build.")
    if manifest.corpus_manifest is None:
        raise ValueError("corpus_manifest is required to build vector indexes.")

    if args.dry_run:
        registry = build_registry(manifest, allow_build=False)
        _print_index_dry_run(registry)
        return 0

    provider = ModelProvider(
        ProviderConfig(
            llm_name=manifest.provider.llm_name,
            embed_model_name=manifest.provider.embed_model_name,
            llm_params=manifest.provider.llm_params,
            embed_params=manifest.provider.embed_params,
        )
    )
    provider.bootstrap_settings(include_embedding=True)
    registry = build_registry(manifest, allow_build=True)
    registry.build_all()
    print(f"Built {len(registry.entries)} vector index(es).")
    return 0


def cmd_evaluate(args: argparse.Namespace) -> int:
    from endorag.evaluation.manifest import load_experiment_manifests
    from endorag.evaluation.runner import EvaluationRunner

    manifests = load_experiment_manifests(args.manifest)
    if args.dry_run:
        from endorag.evaluation.runner import build_registry
        from endorag.evaluation.strategies import load_strategy

        for index, manifest in enumerate(manifests, start=1):
            if manifest.method == "endorag":
                build_registry(manifest, allow_build=False)
                strategy_name = "EndoRAGStrategy"
            else:
                strategy = load_strategy(manifest)
                strategy_name = strategy.__class__.__name__
            print(
                f"[dry-run] {index}/{len(manifests)} name={manifest.name} "
                f"method={manifest.method} strategy={strategy_name} "
                f"dataset={manifest.dataset} output={manifest.output}"
            )
        return 0

    last_accuracy = None
    for index, manifest in enumerate(manifests, start=1):
        print(f"Running experiment {index}/{len(manifests)}: {manifest.name}")
        runner = EvaluationRunner(
            manifest,
            limit=args.limit,
            resume=args.resume,
            overwrite=args.overwrite,
        )
        payload = runner.run(resume=args.resume, overwrite=args.overwrite)
        last_accuracy = payload.get("summary", {}).get("overall_accuracy")
        print(f"Evaluation complete. Overall accuracy: {last_accuracy}")
        print(f"Results saved to {manifest.output}")

    if len(manifests) > 1:
        print(f"Finished {len(manifests)} experiments.")
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    from pathlib import Path

    from endorag.analysis.driver import run_analysis_suite

    manifest = Path(args.manifest).resolve()
    repo_root = manifest.parents[2]
    output_dir = repo_root / "results" / "analysis_exports"
    return run_analysis_suite(manifest, repo_root, output_dir)


def cmd_environment_report(args: argparse.Namespace) -> int:
    from endorag.environment import write_environment_report

    report = write_environment_report(args.output)
    print(f"Environment report written to {args.output}")
    print(f"Python: {report.get('python', '')[:40]}")
    return 0


def main(argv: list[str] | None = None) -> int:
    from endorag.dotenv_loader import load_repo_dotenv

    loaded = load_repo_dotenv()
    if loaded is not None and os.getenv("ENDORAG_VERBOSE_ENV") == "1":
        print(f"Loaded environment from {loaded}", file=sys.stderr)

    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "index" and args.index_command == "build":
        return cmd_index_build(args)
    if args.command == "evaluate":
        return cmd_evaluate(args)
    if args.command == "analyze":
        return cmd_analyze(args)
    if args.command == "environment" and args.environment_command == "report":
        return cmd_environment_report(args)

    parser.error(f"Unhandled command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
