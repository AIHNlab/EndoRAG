#!/usr/bin/env python3
"""Select and copy paper evaluation JSON artifacts from KnowledgeBase into EndoRAG."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO_ROOT / "data" / "manifests" / "paper_results.json"
EXPECTED_TOTAL = 360

PAPER_LLMS = frozenset(
    {
        "gemma4:31b-cloud",
        "nemotron-3-nano:30b-cloud",
        "mistral-small3.2:24b",
        "minimax-m2.7:cloud",
    }
)

EXCLUDED_MODEL_DIRS = frozenset(
    {
        "phi4:latest",
        "qwen3:30b",
        "gemma4:31b",
    }
)

EXPERIMENT_CONFIGS: dict[str, str] = {
    "llm_only": "configs/experiments/llm_only.yaml",
    "embedding_ablation": "configs/experiments/embedding_ablation.yaml",
    "reranker_ablation": "configs/experiments/reranker_ablation.yaml",
    "endorag_main": "configs/experiments/endorag_main.yaml",
    "oracle_routing": "configs/experiments/oracle_routing.yaml",
    "literature_corpus": "configs/experiments/literature_corpus.yaml",
}

RERANK_SOURCE_PREFIX = {
    "Qwen/Qwen3-Reranker-8B": "rerank_qwen8b_Qwen_Qwen3-Reranker-8B_",
    "BAAI/bge-reranker-v2-m3": "rerank_bge_BAAI_bge-reranker-v2-m3_",
    "jinaai/jina-reranker-v3": "rerank_jina_jinaai_jina-reranker-v3_",
    "cross-encoder/ms-marco-MiniLM-L-6-v2": "rerank_ms-marco_cross-encoder_ms-marco-MiniLM-L-6-v2_",
}

LLM_DATASET_PREFIX = {
    "MCQs_sample_questions2015_full": "MCQs_book",
    "UKEU": "UKEU",
    "AdrenalGlands_dataset": "AdrenalGlands",
    "ThyroidGland_dataset": "ThyroidGland",
    "PituitaryGlandAndHypothalamus_dataset": "PituitaryGlandAndHypothalamus",
    "ParathyroidGlandAndBoneDisease_dataset": "ParathyroidGlandAndBoneDisease",
    "ReproductiveEndocrinology_dataset": "ReproductiveEndocrinology",
}

AGENTIC_DATASET_SUFFIX = {
    "MCQs_sample_questions2015_full": "Diabetes",
    "UKEU": "UKEU",
    "AdrenalGlands_dataset": "AdrenalGlands",
    "ThyroidGland_dataset": "ThyroidGland",
    "PituitaryGlandAndHypothalamus_dataset": "PituitaryGlandAndHypothalamus",
    "ParathyroidGlandAndBoneDisease_dataset": "ParathyroidGlandAndBoneDisease",
    "ReproductiveEndocrinology_dataset": "ReproductiveEndocrinology",
}

EXCLUDED_SOURCE_SUFFIXES = (
    "agentic_workflow_eval_AdrenalGlands_dataset.json",
)


def _slug(value: str) -> str:
    return value.replace(":", "_")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_experiments(repo_root: Path) -> list[tuple[str, dict[str, Any]]]:
    entries: list[tuple[str, dict[str, Any]]] = []
    for family, rel_path in EXPERIMENT_CONFIGS.items():
        payload = yaml.safe_load((repo_root / rel_path).read_text(encoding="utf-8"))
        for experiment in payload["experiments"]:
            entries.append((family, experiment))
    return entries


def _dataset_key(dataset_path: str) -> str:
    return Path(dataset_path).stem


def _resolve_llm_source(
    *,
    llm_root: Path,
    model: str,
    dataset_path: str,
) -> Path:
    prefix = LLM_DATASET_PREFIX[_dataset_key(dataset_path)]
    directory = llm_root / model / "LLM"
    matches = sorted(
        path
        for path in directory.glob(f"{prefix}_LLM_*_1.json")
        if path.is_file()
    )
    if not matches:
        raise FileNotFoundError(f"LLM result missing for {model} / {prefix} in {directory}")
    if len(matches) > 1:
        raise RuntimeError(f"Ambiguous LLM matches for {model} / {prefix}: {matches}")
    return matches[0]


def _dataset_prefixes(dataset_path: str) -> list[str]:
    stem = _dataset_key(dataset_path)
    prefixes = [stem]
    llm_prefix = LLM_DATASET_PREFIX.get(stem)
    if llm_prefix and llm_prefix not in prefixes:
        prefixes.append(llm_prefix)
    if stem == "UKEU":
        prefixes.append("UKEU_dataset")
    return prefixes


def _model_tokens(model: str) -> list[str]:
    tokens = [_slug(model)]
    if model not in tokens:
        tokens.append(model)
    return tokens


def _embed_tokens(embedding: str) -> list[str]:
    tokens = [_slug(embedding)]
    if embedding not in tokens:
        tokens.append(embedding)
    return tokens


def _vector_basename_candidates(
    *,
    dataset_path: str,
    model: str,
    embedding: str,
    rerank_model: str | None = None,
) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()
    for dataset_prefix in _dataset_prefixes(dataset_path):
        for model_token in _model_tokens(model):
            for embed_token in _embed_tokens(embedding):
                core = (
                    f"{dataset_prefix}_diabetesVectorTool512_100_{model_token}_{embed_token}_1.json"
                )
                if rerank_model is None:
                    if core not in seen:
                        seen.add(core)
                        candidates.append(core)
                else:
                    name = f"{RERANK_SOURCE_PREFIX[rerank_model]}{core}"
                    if name not in seen:
                        seen.add(name)
                        candidates.append(name)
    return candidates


def _vector_search_dir(
    *,
    vector_root: Path,
    family: str,
    model: str,
    embedding: str,
) -> Path:
    if family == "endorag_main":
        return (
            vector_root
            / model
            / "qwen3-embedding:8b"
            / "agentic_workflow_8B"
        )
    if family == "oracle_routing":
        return vector_root / "oracle" / model / embedding / "LLM" / "Cosine_C512_100"
    if family == "literature_corpus":
        return vector_root / "literature" / model / embedding / "LLM" / "Cosine_C512_100"
    return vector_root / model / embedding / "LLM" / "Cosine_C512_100"


def _resolve_vector_source(
    *,
    vector_root: Path,
    family: str,
    model: str,
    dataset_path: str,
    embedding: str,
    rerank_model: str | None = None,
) -> Path:
    if family == "endorag_main":
        agentic_suffix = AGENTIC_DATASET_SUFFIX[_dataset_key(dataset_path)]
        filename = f"agentic_workflow_eval_{agentic_suffix}.json"
        if filename in EXCLUDED_SOURCE_SUFFIXES:
            raise RuntimeError(f"Refusing excluded EndoRAG artifact: {filename}")
        path = _vector_search_dir(vector_root=vector_root, family=family, model=model, embedding=embedding) / filename
    else:
        directory = _vector_search_dir(
            vector_root=vector_root,
            family=family,
            model=model,
            embedding=embedding,
        )
        path = None
        for candidate in _vector_basename_candidates(
            dataset_path=dataset_path,
            model=model,
            embedding=embedding,
            rerank_model=rerank_model,
        ):
            candidate_path = directory / candidate
            if candidate_path.is_file():
                path = candidate_path
                break
        if path is None:
            prefixes = _dataset_prefixes(dataset_path)
            globber = directory.glob("*.json")
            rerank_prefix = RERANK_SOURCE_PREFIX.get(rerank_model or "", "")
            for candidate_path in sorted(globber):
                name = candidate_path.name
                if name in EXCLUDED_SOURCE_SUFFIXES:
                    continue
                if rerank_model and not name.startswith(rerank_prefix):
                    continue
                if not rerank_model and name.startswith("rerank_"):
                    continue
                if not any(name.startswith(f"{prefix}_") for prefix in prefixes):
                    continue
                if not any(token in name for token in _model_tokens(model)):
                    continue
                if not any(token in name for token in _embed_tokens(embedding)):
                    continue
                path = candidate_path
                break

    if path is None or not path.is_file():
        raise FileNotFoundError(
            _vector_search_dir(
                vector_root=vector_root,
                family=family,
                model=model,
                embedding=embedding,
            )
        )
    if path.name in EXCLUDED_SOURCE_SUFFIXES:
        raise RuntimeError(f"Refusing excluded artifact: {path}")
    return path


def _destination_path(
    *,
    source: Path,
    source_eval_root: Path,
    destination_root: Path,
    dest_method_root: str,
) -> Path:
    relative = source.relative_to(source_eval_root)
    return destination_root / "results" / dest_method_root / relative


def _build_record(
    *,
    family: str,
    experiment: dict[str, Any],
    source: Path,
    destination: Path,
    source_root: Path,
    destination_root: Path,
) -> dict[str, Any]:
    provider = experiment["provider"]
    retrieval = experiment.get("retrieval") or {}
    return {
        "experiment_family": family,
        "experiment_name": experiment["name"],
        "method": experiment["method"],
        "model": provider["llm_name"],
        "embedding": provider.get("embed_model_name"),
        "reranker": retrieval.get("rerank_model"),
        "dataset": _dataset_key(str(experiment["dataset"])),
        "source_path": str(source.relative_to(source_root)),
        "destination_path": str(destination.relative_to(destination_root)),
        "sha256": _sha256_file(source),
    }


def select_paper_results(
    *,
    source_root: Path,
    destination_root: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    source_evaluate = source_root / "evaluate"
    llm_root = source_evaluate / "Method_LLM"
    vector_root = source_evaluate / "Method_vectorRag"
    records: list[dict[str, Any]] = []
    missing: list[dict[str, str]] = []
    family_counts: dict[str, int] = {key: 0 for key in EXPERIMENT_CONFIGS}

    for family, experiment in _load_experiments(REPO_ROOT):
        provider = experiment["provider"]
        model = provider["llm_name"]
        if model not in PAPER_LLMS:
            missing.append(
                {
                    "family": family,
                    "experiment": experiment["name"],
                    "reason": f"skipped non-paper model {model}",
                }
            )
            continue

        try:
            if family == "llm_only":
                source = _resolve_llm_source(
                    llm_root=llm_root,
                    model=model,
                    dataset_path=str(experiment["dataset"]),
                )
                destination = _destination_path(
                    source=source,
                    source_eval_root=llm_root,
                    destination_root=destination_root,
                    dest_method_root="Method_LLM",
                )
            else:
                embedding = provider["embed_model_name"]
                rerank_model = (experiment.get("retrieval") or {}).get("rerank_model")
                source = _resolve_vector_source(
                    vector_root=vector_root,
                    family=family,
                    model=model,
                    dataset_path=str(experiment["dataset"]),
                    embedding=embedding,
                    rerank_model=rerank_model,
                )
                destination = _destination_path(
                    source=source,
                    source_eval_root=vector_root,
                    destination_root=destination_root,
                    dest_method_root="Method_vectorRAG",
                )

            records.append(
                _build_record(
                    family=family,
                    experiment=experiment,
                    source=source,
                    destination=destination,
                    source_root=source_root,
                    destination_root=destination_root,
                )
            )
            family_counts[family] += 1
        except FileNotFoundError as exc:
            missing.append(
                {
                    "family": family,
                    "experiment": experiment["name"],
                    "reason": str(exc),
                }
            )

    inventory = {
        "expected_total": EXPECTED_TOTAL,
        "selected_total": len(records),
        "family_counts": family_counts,
        "missing": missing,
    }
    return records, inventory


def _copy_records(
    records: list[dict[str, Any]],
    *,
    source_root: Path,
    destination_root: Path,
) -> None:
    for record in records:
        source = source_root / record["source_path"]
        destination = destination_root / record["destination_path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if _sha256_file(destination) == record["sha256"]:
                continue
            raise RuntimeError(f"Destination exists with different checksum: {destination}")
        shutil.copy2(source, destination)


def _verify_records(records: list[dict[str, Any]], *, destination_root: Path) -> list[str]:
    errors: list[str] = []
    for record in records:
        destination = destination_root / record["destination_path"]
        if not destination.is_file():
            errors.append(f"missing destination: {destination}")
            continue
        actual = _sha256_file(destination)
        if actual != record["sha256"]:
            errors.append(
                f"checksum mismatch for {destination}: expected {record['sha256']}, got {actual}"
            )
    return errors


def _write_manifest(records: list[dict[str, Any]]) -> None:
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")


def _load_manifest_records() -> list[dict[str, Any]]:
    payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"Manifest must be a list in {MANIFEST_PATH}")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        default=Path("/home/maria/code/KnowledgeBase"),
        help="KnowledgeBase root containing evaluate/Method_* sources",
    )
    parser.add_argument(
        "--destination",
        type=Path,
        default=REPO_ROOT,
        help="EndoRAG repository root receiving results/ and manifest",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify selection counts and destination checksums without copying",
    )
    args = parser.parse_args(argv)

    source_root = args.source.resolve()
    destination_root = args.destination.resolve()

    records, inventory = select_paper_results(
        source_root=source_root,
        destination_root=destination_root,
    )

    status = "OK"
    if inventory["selected_total"] != EXPECTED_TOTAL:
        status = "DONE_WITH_CONCERNS"

    print(f"Status: {status}")
    print(f"Selected: {inventory['selected_total']} / {EXPECTED_TOTAL}")
    for family, count in inventory["family_counts"].items():
        print(f"  {family}: {count}")
    if inventory["missing"]:
        print(f"Missing/unresolved: {len(inventory['missing'])}")
        for item in inventory["missing"][:10]:
            print(f"  - {item['family']}:{item['experiment']}: {item['reason']}")
        if len(inventory["missing"]) > 10:
            print(f"  ... and {len(inventory['missing']) - 10} more")

    if args.check:
        manifest_records = (
            _load_manifest_records() if MANIFEST_PATH.is_file() else records
        )
        if len(manifest_records) != EXPECTED_TOTAL:
            print(
                f"Manifest count mismatch: {len(manifest_records)} != {EXPECTED_TOTAL}",
                file=sys.stderr,
            )
            return 1
        errors = _verify_records(manifest_records, destination_root=destination_root)
        if errors:
            print("Verification errors:", file=sys.stderr)
            for error in errors:
                print(f"  - {error}", file=sys.stderr)
            return 1
        print("Checksum verification passed.")
        return 0

    if records:
        _copy_records(
            records,
            source_root=source_root,
            destination_root=destination_root,
        )
        _write_manifest(records)

    if status != "OK":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
