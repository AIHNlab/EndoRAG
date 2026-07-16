"""Resumable evaluation orchestration."""

from __future__ import annotations

import contextlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from endorag.environment import write_environment_report
from endorag.evaluation.io import (
    build_exact_match_metrics,
    completed_keys,
    is_complete,
    is_partial,
    load_dataset,
    load_results,
    normalize_expected_output,
    result_key,
    retrieval_context_strings,
    save_results_atomic,
)
from endorag.evaluation.manifest import ExperimentManifest
from endorag.evaluation.models import EvaluationStrategy, Prediction
from endorag.providers.models import ModelProvider, ProviderConfig
from endorag.retrieval.routing import (
    infer_routing_category_from_dataset_path,
    question_category_map_path_for_dataset,
)


class _TeeStream:
    """Write evaluation output to both the terminal and the manifest log."""

    def __init__(self, terminal: Any, log_handle: Any) -> None:
        self.terminal = terminal
        self.log_handle = log_handle

    def write(self, value: str) -> int:
        self.terminal.write(value)
        self.log_handle.write(value)
        return len(value)

    def flush(self) -> None:
        self.terminal.flush()
        self.log_handle.flush()

    def __getattr__(self, name: str) -> Any:
        return getattr(self.terminal, name)


class EvaluationRunner:
    def __init__(
        self,
        manifest: ExperimentManifest,
        *,
        limit: int | None = None,
        resume: bool = False,
        overwrite: bool = False,
    ) -> None:
        self.manifest = manifest
        self.limit = limit
        self.resume = resume
        self.overwrite = overwrite
        self._timing_info: dict[str, Any] | None = None

    def run(self, resume: bool = False, overwrite: bool = False) -> dict[str, Any]:
        resume = resume or self.resume
        overwrite = overwrite or self.overwrite
        dataset = load_dataset(self.manifest.dataset)
        if self.limit is not None:
            dataset = dataset[: self.limit]

        existing = load_results(self.manifest.output)
        if resume and existing is not None:
            if is_complete(existing, len(dataset)):
                print(
                    f"Skipping completed experiment {self.manifest.name}: "
                    f"{self.manifest.output}",
                    flush=True,
                )
                return existing
            self._validate_resume_compatibility(existing, dataset)

        self._validate_output_state(
            resume=resume,
            overwrite=overwrite,
            expected_count=len(dataset),
        )

        log_path = self.manifest.log
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_mode = "a" if resume and not overwrite else "w"
        with log_path.open(log_mode, encoding="utf-8") as log_handle:
            stdout = _TeeStream(sys.stdout, log_handle)
            stderr = _TeeStream(sys.stderr, log_handle)
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                print(
                    f"=== EndoRAG experiment {self.manifest.name} "
                    f"started {datetime.now(timezone.utc).isoformat()} ===",
                    flush=True,
                )
                return self._run_evaluation(
                    dataset,
                    resume=resume,
                    overwrite=overwrite,
                )

    def _run_evaluation(
        self,
        dataset: list[dict[str, Any]],
        *,
        resume: bool,
        overwrite: bool,
    ) -> dict[str, Any]:
        existing = None if overwrite else load_results(self.manifest.output)
        results_by_key = {}
        if existing and resume:
            for record in existing.get("results") or []:
                results_by_key[result_key(record)] = record

        provider = ModelProvider(
            ProviderConfig(
                llm_name=self.manifest.provider.llm_name,
                embed_model_name=self.manifest.provider.embed_model_name,
                llm_params={
                    "seed": self.manifest.seed,
                    **self.manifest.provider.llm_params,
                },
                embed_params={
                    "seed": self.manifest.seed,
                    **self.manifest.provider.embed_params,
                },
            )
        )
        include_embedding = self.manifest.method != "llm"
        provider.bootstrap_settings(include_embedding=include_embedding)

        from endorag.evaluation.strategies import load_strategy

        strategy = load_strategy(self.manifest)

        environment_path = self.manifest.output.with_suffix(".environment.json")
        environment_report = write_environment_report(environment_path)

        payload: dict[str, Any] = {
            "summary": {
                "overall_accuracy": (existing or {}).get("summary", {}).get("overall_accuracy", 0.0),
                "complete": False,
                "expected_count": len(dataset),
                "completed_count": len(completed_keys(list(results_by_key.values()))),
            },
            "category_accuracy": (existing or {}).get("category_accuracy", {}),
            "timing": (existing or {}).get("timing", {}),
            "resolved_manifest": self.manifest.model_dump(mode="json"),
            "environment_report": environment_report,
            "results": list(results_by_key.values()),
        }

        eval_start = time.time()
        question_times: list[float] = []
        done_keys = completed_keys(payload["results"])

        for index, example in enumerate(dataset):
            question = str(example.get("input", "")).strip()
            expected_output = normalize_expected_output(example.get("expected_output", ""))
            record_id = str(example.get("id") or f"{index + 1:03d}")
            key = str(example.get("id") or question)

            if key in done_keys and resume:
                continue

            q_start = time.time()
            prediction: Prediction
            try:
                prediction = strategy.answer(
                    question,
                    context={
                        "id": record_id,
                        "expected_output": expected_output,
                        "index": index,
                    },
                )
            except Exception as exc:  # noqa: BLE001
                prediction = Prediction(
                    actual_output="",
                    flow_diagnostics={"workflow_error": str(exc)},
                )
                record = self._build_result_record(
                    record_id=record_id,
                    question=question,
                    expected_output=expected_output,
                    prediction=prediction,
                )
                results_by_key[key] = record
                payload["results"] = list(results_by_key.values())
                payload["summary"]["completed_count"] = len(completed_keys(payload["results"]))
                save_results_atomic(self.manifest.output, payload)
                raise

            record = self._build_result_record(
                record_id=record_id,
                question=question,
                expected_output=expected_output,
                prediction=prediction,
            )
            results_by_key[key] = record
            payload["results"] = list(results_by_key.values())
            payload["summary"]["completed_count"] = len(completed_keys(payload["results"]))
            save_results_atomic(self.manifest.output, payload)

            question_times.append(time.time() - q_start)
            done_keys.add(key)

            if self.manifest.method == "endorag" and prediction.flow_diagnostics.get(
                "workflow_error"
            ):
                break

        total_eval_time = time.time() - eval_start
        avg_time = total_eval_time / len(question_times) if question_times else 0.0
        self._timing_info = {
            "total_seconds": round(total_eval_time, 1),
            "avg_per_question_seconds": round(avg_time, 1),
            "min_question_seconds": round(min(question_times), 1) if question_times else 0.0,
            "max_question_seconds": round(max(question_times), 1) if question_times else 0.0,
            "per_question_seconds": [round(value, 1) for value in question_times],
        }

        overall_accuracy, category_accuracy = self._aggregate_metrics(
            payload["results"],
            dataset=dataset,
            manifest=self.manifest,
        )
        payload["summary"] = {
            "overall_accuracy": round(overall_accuracy, 4),
            "complete": is_complete({"results": payload["results"]}, len(dataset)),
            "expected_count": len(dataset),
            "completed_count": len(completed_keys(payload["results"])),
            "timing": self._timing_info,
            "category_accuracy": category_accuracy,
        }
        payload["category_accuracy"] = category_accuracy
        payload["timing"] = self._timing_info
        save_results_atomic(self.manifest.output, payload)
        return payload

    def _validate_output_state(
        self,
        *,
        resume: bool,
        overwrite: bool,
        expected_count: int,
    ) -> None:
        existing = load_results(self.manifest.output)
        if existing is None:
            return
        if is_complete(existing, expected_count):
            if resume:
                return
            if not overwrite:
                raise FileExistsError(
                    f"Complete results already exist at {self.manifest.output}; "
                    "pass --overwrite to replace them."
                )
            return
        if is_partial(existing, expected_count) and not resume and not overwrite:
            raise FileExistsError(
                f"Partial results already exist at {self.manifest.output}; "
                "pass --resume to continue or --overwrite to restart."
            )

    def _validate_resume_compatibility(
        self,
        existing: dict[str, Any],
        dataset: list[dict[str, Any]],
    ) -> None:
        saved_manifest = existing.get("resolved_manifest")
        current_manifest = self.manifest.model_dump(mode="json")
        if not isinstance(saved_manifest, dict):
            raise ValueError(
                f"Cannot safely resume {self.manifest.output}: the partial result "
                "does not contain a resolved_manifest. Use --overwrite to restart."
            )
        if saved_manifest != current_manifest:
            raise ValueError(
                f"Cannot resume {self.manifest.output}: its saved manifest differs "
                "from the requested experiment. Use --overwrite to restart."
            )

        dataset_by_id = {
            str(example.get("id")): example
            for example in dataset
            if example.get("id") is not None
        }
        dataset_by_question = {
            str(example.get("input", "")).strip(): example for example in dataset
        }
        seen_keys: set[str] = set()
        for record in existing.get("results") or []:
            key = result_key(record)
            if key in seen_keys:
                raise ValueError(
                    f"Cannot safely resume {self.manifest.output}: duplicate result "
                    f"key {key!r}. Use --overwrite to restart."
                )
            seen_keys.add(key)
            example = dataset_by_id.get(str(record.get("id")))
            if example is None:
                example = dataset_by_question.get(
                    str(record.get("input", "")).strip()
                )
            if example is None:
                raise ValueError(
                    f"Cannot resume {self.manifest.output}: saved record {key!r} "
                    "is not present in the current dataset. Use --overwrite to restart."
                )
            saved_question = str(record.get("input", "")).strip()
            current_question = str(example.get("input", "")).strip()
            saved_expected = normalize_expected_output(
                record.get("expected_output", "")
            )
            current_expected = normalize_expected_output(
                example.get("expected_output", "")
            )
            if saved_question != current_question or saved_expected != current_expected:
                raise ValueError(
                    f"Cannot resume {self.manifest.output}: dataset content changed "
                    f"for record {key!r}. Use --overwrite to restart."
                )

    @staticmethod
    def _build_result_record(
        *,
        record_id: str,
        question: str,
        expected_output: str,
        prediction: Prediction,
    ) -> dict[str, Any]:
        actual_output = normalize_expected_output(prediction.actual_output)
        return {
            "id": record_id,
            "input": question,
            "expected_output": expected_output,
            "actual_output": actual_output,
            "retrieval_context": prediction.retrieval_context,
            "retrieval_context_strings": retrieval_context_strings(
                prediction.retrieval_context
            ),
            "flow_diagnostics": prediction.flow_diagnostics,
            "routing_category": prediction.routing_category,
            "metrics": build_exact_match_metrics(actual_output, expected_output),
        }

    @staticmethod
    def _aggregate_metrics(
        results: list[dict[str, Any]],
        *,
        dataset: list[dict[str, Any]],
        manifest: ExperimentManifest,
    ) -> tuple[float, dict[str, Any]]:
        total_score = 0.0
        category_stats: dict[str, dict[str, int]] = {}
        oracle_map = _load_oracle_map(manifest)
        dataset_pin = infer_routing_category_from_dataset_path(str(manifest.dataset))

        for record in results:
            metrics = record.get("metrics") or []
            score = float(metrics[0]["score"]) if metrics else 0.0
            total_score += score
            category = _metrics_category_label(
                record,
                manifest=manifest,
                oracle_map=oracle_map,
                dataset_pin=dataset_pin,
            )
            if category not in category_stats:
                category_stats[category] = {"correct": 0, "total": 0}
            category_stats[category]["total"] += 1
            if score == 1.0:
                category_stats[category]["correct"] += 1

        overall = total_score / len(results) if results else 0.0
        category_accuracy = {
            category: {
                "accuracy": round(stats["correct"] / stats["total"], 4)
                if stats["total"] > 0
                else 0.0,
                "correct": stats["correct"],
                "total": stats["total"],
            }
            for category, stats in sorted(category_stats.items())
        }
        return overall, category_accuracy


def _load_oracle_map(manifest: ExperimentManifest) -> dict[str, str]:
    if not manifest.oracle_routing:
        return {}
    path = question_category_map_path_for_dataset(str(manifest.dataset))
    if not Path(path).is_file():
        return {}
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _metrics_category_label(
    record: dict[str, Any],
    *,
    manifest: ExperimentManifest,
    oracle_map: dict[str, str],
    dataset_pin: str | None,
) -> str:
    question = str(record.get("input", "")).strip()
    if manifest.oracle_routing:
        if dataset_pin:
            return dataset_pin
        return oracle_map.get(question, "Unknown")
    if manifest.method == "vector-rag":
        return record.get("routing_category") or "Unknown"
    if manifest.method == "endorag":
        flow_diagnostics = record.get("flow_diagnostics") or {}
        return flow_diagnostics.get("routed_category") or "Unknown"
    return oracle_map.get(question, "Unknown")


def build_vector_args(manifest: ExperimentManifest) -> Any:
    from types import SimpleNamespace

    retrieval = manifest.retrieval
    if retrieval is None:
        raise ValueError("Retrieval settings are required for vector-backed methods.")
    return SimpleNamespace(
        db_config=str(manifest.corpus_manifest) if manifest.corpus_manifest else None,
        embed_model_name=manifest.provider.embed_model_name,
        top_k=retrieval.top_k,
        use_rerank=retrieval.use_rerank,
        rerank_top_n=retrieval.rerank_top_n,
        rerank_model=retrieval.rerank_model or "Qwen/Qwen3-Reranker-8B",
        rerank_config=os.getenv("ENDORAG_RERANK_CONFIG", "configs/rerank.yaml"),
        rerank_candidate_multiplier=retrieval.candidate_multiplier,
        chroma_root=os.getenv("ENDORAG_CHROMA_ROOT", ""),
        doc_transformations=_transformations_from_retrieval(retrieval),
        docling_config=os.getenv("ENDORAG_DOCLING_CONFIG", "configs/docling.yaml"),
    )


def _transformations_from_retrieval(retrieval: Any) -> list[list[Any]]:
    return [
        [
            "llama_index.core.node_parser.SentenceSplitter",
            f"{{'chunk_size': {retrieval.chunk_size}, 'chunk_overlap': {retrieval.chunk_overlap}}}",
        ]
    ]


def build_registry(manifest: ExperimentManifest, *, allow_build: bool = False) -> Any:
    from endorag.retrieval.chroma_paths import infer_chunk_db_segment
    from endorag.retrieval.query_engine import QueryEngineOptions
    from endorag.retrieval.registry import VectorDbRegistry

    retrieval = manifest.retrieval
    if retrieval is None:
        raise ValueError("Retrieval settings are required.")
    if manifest.corpus_manifest is None:
        raise ValueError("corpus_manifest is required for vector-backed methods.")

    transformations = _transformations_from_retrieval(retrieval)
    chunk_segment = infer_chunk_db_segment(transformations)
    query_options = QueryEngineOptions(
        top_k=retrieval.top_k,
        use_rerank=retrieval.use_rerank,
        rerank_top_n=retrieval.rerank_top_n,
        rerank_model=retrieval.rerank_model or "Qwen/Qwen3-Reranker-8B",
        rerank_config=os.getenv("ENDORAG_RERANK_CONFIG", "configs/rerank.yaml"),
        candidate_multiplier=retrieval.candidate_multiplier,
    )
    return VectorDbRegistry.from_manifest(
        manifest.corpus_manifest,
        embed_model=str(manifest.provider.embed_model_name),
        chroma_root=os.getenv("ENDORAG_CHROMA_ROOT", ""),
        chunk_segment=chunk_segment,
        transformations=transformations,
        docling_config=os.getenv("ENDORAG_DOCLING_CONFIG", "configs/docling.yaml"),
        query_options=query_options,
        allow_build=allow_build,
    )
