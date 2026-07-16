"""Load and persist evaluation datasets and result files."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from endorag.evaluation.metrics import exact_match, normalize_mcq_letter


def load_dataset(path: str | Path) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"Dataset must be a JSON list: {path}")
    return payload


def load_results(path: str | Path) -> dict[str, Any] | None:
    destination = Path(path)
    if not destination.is_file():
        return None
    payload = json.loads(destination.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Results file must contain a JSON object: {path}")
    return payload


def _json_default(obj: Any) -> Any:
    """Serialize Path and other common non-JSON types found in diagnostics."""
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, set):
        return sorted(obj)
    if hasattr(obj, "isoformat"):
        try:
            return obj.isoformat()
        except (TypeError, ValueError):
            pass
    raise TypeError(f"Object of type {obj.__class__.__name__} is not JSON serializable")


def save_results_atomic(path: str | Path, payload: dict[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(
        payload, indent=4, ensure_ascii=False, default=_json_default
    )
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=destination.parent,
        delete=False,
    ) as handle:
        handle.write(text)
        temp_path = handle.name
    os.replace(temp_path, destination)


def result_key(record: dict[str, Any]) -> str:
    if record.get("id"):
        return str(record["id"])
    return str(record.get("input", "")).strip()


def _has_completed_output(record: dict[str, Any]) -> bool:
    value = record.get("actual_output")
    return value is not None and str(value).strip() != ""


def completed_keys(results: list[dict[str, Any]]) -> set[str]:
    return {result_key(record) for record in results if _has_completed_output(record)}


def is_complete(payload: dict[str, Any], expected_count: int) -> bool:
    results = payload.get("results") or []
    if len(results) < expected_count:
        return False
    return all(_has_completed_output(record) for record in results[:expected_count])


def is_partial(payload: dict[str, Any], expected_count: int) -> bool:
    results = payload.get("results") or []
    if not results:
        return False
    return not is_complete(payload, expected_count)


def retrieval_context_strings(retrieval_context: list[Any] | None) -> list[str]:
    strings: list[str] = []
    for item in retrieval_context or []:
        if isinstance(item, str):
            strings.append(item)
        elif isinstance(item, dict):
            source = item.get("source", "Unknown source")
            score = item.get("score")
            text = item.get("text", "")
            query = item.get("query")
            strings.append(
                f"Source: {source}\nScore: {score}\nQuery: {query}\nContent: {text}"
            )
        else:
            strings.append(str(item))
    return strings


def build_exact_match_metrics(actual: object, expected: object) -> list[dict[str, Any]]:
    score = exact_match(actual, expected)
    return [
        {
            "metric": "ExactMatch",
            "score": score,
            "reason": "Correct" if score == 1.0 else "Incorrect",
        }
    ]


def normalize_expected_output(value: object) -> str:
    return normalize_mcq_letter(value)
