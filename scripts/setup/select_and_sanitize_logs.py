#!/usr/bin/env python3
"""Select, sanitize, and copy paper-aligned evaluation logs into EndoRAG."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

from select_paper_results import (
    AGENTIC_DATASET_SUFFIX,
    EXPERIMENT_CONFIGS,
    LLM_DATASET_PREFIX,
    PAPER_LLMS,
    _dataset_key,
    _load_experiments,
    _slug,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO_ROOT / "data" / "manifests" / "paper_logs.json"
ENDORAG_RESULT_JSON_COUNT = 28

ALLOWED_LOG_PREFIXES = (
    "Cosine_C512_100/",
    "Cosine_C512_100/rerank/qwen-8b/",
    "Cosine_C512_100/rerank/bge/",
    "Cosine_C512_100/rerank/jina/",
    "Cosine_C512_100/rerank/ms-marco/",
    "Cosine_C512_100/rerank/literature/qwen-8b/",
    "Cosine_C512_100/rerank_oracle/qwen-8b/",
    "agentic_workflow/",
)

EXCLUDED_NAME_PATTERNS = (
    re.compile(r"phi4", re.I),
    re.compile(r"qwen3:30b|qwen3_30b", re.I),
    re.compile(r"gemma4:31b[^-]|gemma4_31b[^-]", re.I),
    re.compile(r"qwen3-reranker-4b|rerank-4b", re.I),
    re.compile(r"qwen3\.5", re.I),
    re.compile(r"gpt-oss", re.I),
    re.compile(r"BM25|MMR|C400|C1024"),
)

RERANK_LOG_DIRS = {
    "Qwen/Qwen3-Reranker-8B": "rerank/qwen-8b",
    "BAAI/bge-reranker-v2-m3": "rerank/bge",
    "jinaai/jina-reranker-v3": "rerank/jina",
    "cross-encoder/ms-marco-MiniLM-L-6-v2": "rerank/ms-marco",
}

SENSITIVE_PATTERNS = (
    re.compile(r"sk-proj-"),
    re.compile(r"Authorization:"),
    re.compile(r"Bearer [A-Za-z0-9._-]{12,}"),
    re.compile(r"knowledgegroup"),
    re.compile(r"/home/maria/"),
    re.compile(r"192\.168\."),
)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalize_tokens(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _model_tokens(model: str) -> list[str]:
    tokens = {_slug(model), model, model.replace(":", "_"), model.replace(":", "-")}
    return [token for token in tokens if token]


def _embed_tokens(embedding: str) -> list[str]:
    tokens = {
        _slug(embedding),
        embedding,
        embedding.replace(":", "_"),
        embedding.replace(":", "-"),
        embedding.replace(":", ""),
    }
    return [token for token in tokens if token]


def _dataset_tokens(dataset_path: str) -> list[str]:
    stem = _dataset_key(dataset_path)
    tokens = {stem, stem.replace("_dataset", "")}
    llm_prefix = LLM_DATASET_PREFIX.get(stem)
    if llm_prefix:
        tokens.add(llm_prefix)
    if stem == "UKEU":
        tokens.add("UKEU_dataset")
    return sorted(tokens)


def _is_excluded_name(name: str) -> bool:
    return any(pattern.search(name) for pattern in EXCLUDED_NAME_PATTERNS)


def _matches_tokens(name: str, tokens: list[str]) -> bool:
    normalized_name = _normalize_tokens(name)
    return any(_normalize_tokens(token) in normalized_name for token in tokens)


def _pick_candidate(candidates: list[Path], *, preferred_name: str | None = None) -> Path | None:
    if not candidates:
        return None
    if preferred_name:
        preferred_norm = _normalize_tokens(preferred_name)
        for candidate in candidates:
            if _normalize_tokens(candidate.name) == preferred_norm:
                return candidate
        for candidate in candidates:
            if preferred_norm in _normalize_tokens(candidate.name):
                return candidate
    if len(candidates) == 1:
        return candidates[0]
    return sorted(candidates, key=lambda path: (len(path.name), path.name))[0]


def _resolve_llm_log(source_logs: Path, *, model: str, dataset_path: str) -> Path | None:
    prefix = LLM_DATASET_PREFIX[_dataset_key(dataset_path)]
    candidates = [
        path
        for path in source_logs.glob(f"{prefix}_LLM_*_1.log")
        if path.is_file() and _matches_tokens(path.name, _model_tokens(model))
    ]
    preferred = f"{prefix}_LLM_{model.replace(':', '_')}_1.log"
    return _pick_candidate(candidates, preferred_name=preferred)


def _resolve_cosine_root_log(
    source_logs: Path,
    *,
    model: str,
    dataset_path: str,
    embedding: str,
) -> Path | None:
    candidates = [
        path
        for path in (source_logs / "Cosine_C512_100").glob("*.log")
        if path.is_file()
        and not _is_excluded_name(path.name)
        and "_rerank_" not in path.name
        and _matches_tokens(path.name, _dataset_tokens(dataset_path))
        and _matches_tokens(path.name, _model_tokens(model))
        and _matches_tokens(path.name, _embed_tokens(embedding))
    ]
    return _pick_candidate(candidates)


def _resolve_rerank_log(
    source_logs: Path,
    *,
    model: str,
    dataset_path: str,
    embedding: str,
    rerank_model: str,
) -> Path | None:
    subdir = RERANK_LOG_DIRS[rerank_model]
    search_dir = source_logs / "Cosine_C512_100" / subdir
    if not search_dir.is_dir():
        return None
    candidates = [
        path
        for path in search_dir.glob("*.log")
        if path.is_file()
        and not _is_excluded_name(path.name)
        and _matches_tokens(path.name, _dataset_tokens(dataset_path))
        and _matches_tokens(path.name, _model_tokens(model))
        and _matches_tokens(path.name, _embed_tokens(embedding))
    ]
    return _pick_candidate(candidates)


def _resolve_oracle_log(
    source_logs: Path,
    *,
    model: str,
    dataset_path: str,
    embedding: str,
) -> Path | None:
    search_dir = source_logs / "Cosine_C512_100" / "rerank_oracle" / "qwen-8b"
    candidates = [
        path
        for path in search_dir.glob("*.log")
        if path.is_file()
        and not _is_excluded_name(path.name)
        and _matches_tokens(path.name, _dataset_tokens(dataset_path))
        and _matches_tokens(path.name, _model_tokens(model))
        and _matches_tokens(path.name, _embed_tokens(embedding))
    ]
    return _pick_candidate(candidates)


def _resolve_literature_log(
    source_logs: Path,
    *,
    model: str,
    dataset_path: str,
    embedding: str,
) -> Path | None:
    search_dir = source_logs / "Cosine_C512_100" / "rerank" / "literature" / "qwen-8b"
    candidates = [
        path
        for path in search_dir.glob("*.log")
        if path.is_file()
        and not _is_excluded_name(path.name)
        and _matches_tokens(path.name, _dataset_tokens(dataset_path))
        and _matches_tokens(path.name, _model_tokens(model))
        and _matches_tokens(path.name, _embed_tokens(embedding))
    ]
    return _pick_candidate(candidates)


def _resolve_endorag_log(source_logs: Path, *, dataset_path: str) -> Path | None:
    suffix = AGENTIC_DATASET_SUFFIX[_dataset_key(dataset_path)]
    path = source_logs / "agentic_workflow" / f"agentic_workflow_eval_{suffix}.log"
    return path if path.is_file() else None


def _destination_for_log(config_log: str) -> Path:
    rel = config_log.removeprefix("logs/")
    if rel.startswith("Cosine_C512_100/endorag/"):
        dataset_part = Path(rel).name
        for dataset, suffix in AGENTIC_DATASET_SUFFIX.items():
            if dataset_part.startswith(dataset.replace("_dataset", "")) or dataset in dataset_part:
                return Path("agentic_workflow") / f"agentic_workflow_eval_{suffix}.log"
        stem = dataset_part.split("_diabetesVectorTool512_100_")[0]
        if stem.startswith("MCQs"):
            return Path("agentic_workflow") / "agentic_workflow_eval_Diabetes.log"
        if stem == "UKEU":
            return Path("agentic_workflow") / "agentic_workflow_eval_UKEU.log"
    return Path(rel)


def _resolve_source_log(
    source_logs: Path,
    *,
    family: str,
    experiment: dict[str, Any],
) -> Path | None:
    provider = experiment["provider"]
    model = provider["llm_name"]
    dataset_path = str(experiment["dataset"])
    if family == "llm_only":
        return _resolve_llm_log(source_logs, model=model, dataset_path=dataset_path)
    embedding = provider["embed_model_name"]
    if family == "embedding_ablation":
        return _resolve_cosine_root_log(
            source_logs,
            model=model,
            dataset_path=dataset_path,
            embedding=embedding,
        )
    rerank_model = (experiment.get("retrieval") or {}).get("rerank_model")
    if family == "reranker_ablation" and rerank_model:
        return _resolve_rerank_log(
            source_logs,
            model=model,
            dataset_path=dataset_path,
            embedding=embedding,
            rerank_model=rerank_model,
        )
    if family == "endorag_main":
        return _resolve_endorag_log(source_logs, dataset_path=dataset_path)
    if family == "oracle_routing":
        return _resolve_oracle_log(
            source_logs,
            model=model,
            dataset_path=dataset_path,
            embedding=embedding,
        )
    if family == "literature_corpus":
        return _resolve_literature_log(
            source_logs,
            model=model,
            dataset_path=dataset_path,
            embedding=embedding,
        )
    return None


def _infer_experiment_family(relative_path: str) -> str:
    if relative_path.startswith("agentic_workflow/"):
        return "endorag_main"
    if "/rerank/literature/" in relative_path:
        return "literature_corpus"
    if "/rerank_oracle/" in relative_path:
        return "oracle_routing"
    if "/rerank/" in relative_path:
        return "reranker_ablation"
    if relative_path.startswith("Cosine_C512_100/"):
        return "embedding_ablation"
    return "llm_only"


def sanitize_log_text(text: str) -> str:
    text = text.replace("/home/maria/code/KnowledgeBase", "$SOURCE_REPOSITORY")
    text = re.sub(r"Authorization:\s*\S+", "Authorization: [REDACTED]", text)
    text = re.sub(r"Bearer\s+[A-Za-z0-9._-]+", "Bearer [REDACTED]", text)
    text = re.sub(
        r"(?i)(api[_-]?key\s*[=:]\s*)\S+",
        r"\1[REDACTED]",
        text,
    )
    text = re.sub(r"sk-proj-[A-Za-z0-9._-]+", "[REDACTED]", text)
    text = re.sub(
        r"/home/maria/[^\s\"']*chroma[^\s\"']*",
        "$ENDORAG_CHROMA_ROOT",
        text,
        flags=re.I,
    )
    text = re.sub(r"/home/maria/[^\s\"']+", "$ENDORAG_DOCUMENT_ROOT", text)
    text = re.sub(r"\blocalhost\b", "[REDACTED_HOST]", text)
    text = re.sub(r"\b127\.0\.0\.1\b", "[REDACTED_HOST]", text)
    text = re.sub(r"\b192\.168\.\d{1,3}\.\d{1,3}\b", "[REDACTED_HOST]", text)
    return text


def _scan_sensitive_text(text: str) -> list[str]:
    hits: list[str] = []
    for pattern in SENSITIVE_PATTERNS:
        if pattern.search(text):
            hits.append(pattern.pattern)
    return hits


def select_paper_logs(
    *,
    source_logs: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    records_by_destination: dict[str, dict[str, Any]] = {}
    missing: list[dict[str, str]] = []
    family_counts: dict[str, int] = {key: 0 for key in EXPERIMENT_CONFIGS}

    for family, experiment in _load_experiments(REPO_ROOT):
        provider = experiment["provider"]
        model = provider["llm_name"]
        if model not in PAPER_LLMS:
            continue

        config_log = experiment.get("log")
        if not config_log:
            missing.append(
                {
                    "family": family,
                    "experiment": experiment["name"],
                    "reason": "experiment config missing log path",
                }
            )
            continue

        source = _resolve_source_log(
            source_logs,
            family=family,
            experiment=experiment,
        )
        if source is None:
            missing.append(
                {
                    "family": family,
                    "experiment": experiment["name"],
                    "reason": f"log not found for {config_log}",
                }
            )
            continue

        destination_rel = _destination_for_log(config_log)
        source_rel = str(source.relative_to(source_logs))
        if not any(source_rel.startswith(prefix) for prefix in ALLOWED_LOG_PREFIXES):
            if family == "llm_only" and "/" not in source_rel:
                pass
            else:
                missing.append(
                    {
                        "family": family,
                        "experiment": experiment["name"],
                        "reason": f"resolved log outside allowed prefixes: {source_rel}",
                    }
                )
                continue

        original_bytes = source.read_bytes()
        source_text = source.read_text(encoding="utf-8", errors="replace")
        sanitized_text = sanitize_log_text(source_text)
        sanitized_bytes = sanitized_text.encode("utf-8")
        dest_key = str(destination_rel)
        record = {
            "experiment_family": family,
            "experiment_name": experiment["name"],
            "model": model,
            "embedding": provider.get("embed_model_name"),
            "reranker": (experiment.get("retrieval") or {}).get("rerank_model"),
            "dataset": _dataset_key(str(experiment["dataset"])),
            "source_path": source_rel,
            "destination_path": str(Path("logs") / destination_rel),
            "original_sha256": _sha256_bytes(original_bytes),
            "sanitized_sha256": _sha256_bytes(sanitized_bytes),
        }
        existing = records_by_destination.get(dest_key)
        if existing:
            if existing["original_sha256"] != record["original_sha256"]:
                raise RuntimeError(
                    f"Conflicting sources for {dest_key}: {existing['source_path']} vs {source_rel}"
                )
            continue
        records_by_destination[dest_key] = record
        family_counts[family] += 1

    records = sorted(records_by_destination.values(), key=lambda item: item["destination_path"])
    endorag_logs = [
        record
        for record in records
        if record["destination_path"].startswith("logs/agentic_workflow/")
    ]
    inventory = {
        "selected_total": len(records),
        "family_counts": family_counts,
        "endorag_result_json_count": ENDORAG_RESULT_JSON_COUNT,
        "endorag_log_count": len(set(record["destination_path"] for record in endorag_logs)),
        "endorag_note": (
            "Only seven historical EndoRAG agentic_workflow logs exist in the source "
            f"repository even though {ENDORAG_RESULT_JSON_COUNT} EndoRAG result JSONs "
            "were retained in Task 8."
        ),
        "missing": missing,
    }
    return records, inventory


def _write_sanitized_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    sanitized = sanitize_log_text(source.read_text(encoding="utf-8", errors="replace"))
    destination.write_text(sanitized, encoding="utf-8")


def _destination_file(record: dict[str, Any], *, destination_logs: Path) -> Path:
    rel = Path(record["destination_path"]).relative_to("logs")
    return destination_logs / rel


def _copy_records(
    records: list[dict[str, Any]],
    *,
    source_logs: Path,
    destination_logs: Path,
) -> None:
    for record in records:
        source = source_logs / record["source_path"]
        destination = _destination_file(record, destination_logs=destination_logs)
        if destination.exists():
            if _sha256_file(destination) == record["sanitized_sha256"]:
                continue
            destination.unlink()
        _write_sanitized_copy(source, destination)


def _verify_records(records: list[dict[str, Any]], *, destination_logs: Path) -> list[str]:
    errors: list[str] = []
    for record in records:
        destination = _destination_file(record, destination_logs=destination_logs)
        if not destination.is_file():
            errors.append(f"missing destination: {destination}")
            continue
        actual = _sha256_file(destination)
        if actual != record["sanitized_sha256"]:
            errors.append(
                f"checksum mismatch for {destination}: expected {record['sanitized_sha256']}, got {actual}"
            )
        sensitive_hits = _scan_sensitive_text(destination.read_text(encoding="utf-8", errors="replace"))
        if sensitive_hits:
            errors.append(
                f"sensitive data remains in {destination}: {', '.join(sorted(set(sensitive_hits)))}"
            )
    return errors


def _write_manifest(records: list[dict[str, Any]], inventory: dict[str, Any]) -> None:
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {"inventory": inventory, "records": records}
    MANIFEST_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _load_manifest() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return payload, {}
    return payload["records"], payload.get("inventory", {})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        default=Path("/home/maria/code/KnowledgeBase/logs"),
        help="KnowledgeBase logs root",
    )
    parser.add_argument(
        "--destination",
        type=Path,
        default=REPO_ROOT / "logs",
        help="Output directory for sanitized .log files (EndoRAG logs/)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify manifest and sanitized destination checksums without copying",
    )
    args = parser.parse_args(argv)

    source_logs = args.source.resolve()
    destination_logs = args.destination.resolve()

    records, inventory = select_paper_logs(
        source_logs=source_logs,
    )

    status = "OK"
    if inventory["endorag_log_count"] != 7:
        status = "DONE_WITH_CONCERNS"
    if inventory["missing"]:
        status = "DONE_WITH_CONCERNS"

    print(f"Status: {status}")
    print(f"Selected: {inventory['selected_total']}")
    for family, count in inventory["family_counts"].items():
        if count:
            print(f"  {family}: {count}")
    print(f"EndoRAG logs: {inventory['endorag_log_count']} / {inventory['endorag_result_json_count']} result JSONs")
    if inventory["missing"]:
        print(f"Missing/unresolved experiments: {len(inventory['missing'])}")
        for item in inventory["missing"][:10]:
            print(f"  - {item['family']}:{item['experiment']}: {item['reason']}")
        if len(inventory["missing"]) > 10:
            print(f"  ... and {len(inventory['missing']) - 10} more")

    if args.check:
        if not MANIFEST_PATH.is_file():
            print(f"Manifest missing: {MANIFEST_PATH}", file=sys.stderr)
            return 1
        manifest_records, manifest_inventory = _load_manifest()
        if len(manifest_records) != len(records):
            print(
                f"Manifest count mismatch: {len(manifest_records)} != {len(records)}",
                file=sys.stderr,
            )
            return 1
        errors = _verify_records(manifest_records, destination_logs=destination_logs)
        if errors:
            print("Verification errors:", file=sys.stderr)
            for error in errors:
                print(f"  - {error}", file=sys.stderr)
            return 1
        if manifest_inventory.get("endorag_log_count", 0) != 7:
            print("Expected seven EndoRAG logs in manifest inventory.", file=sys.stderr)
            return 1
        print("Checksum and sanitization verification passed.")
        return 0

    if records:
        _copy_records(records, source_logs=source_logs, destination_logs=destination_logs)
        _write_manifest(records, inventory)

    if status != "OK":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
