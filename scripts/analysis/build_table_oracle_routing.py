#!/usr/bin/env python3
"""Compare single-pass RAG + reranking accuracy with vs without oracle routing."""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from endorag.analysis.artifact_resolver import RERANK_PREFIX, VECTOR_SUBDIR, get_resolver
from endorag.analysis.cli_args import add_paper_analysis_args, resolve_analysis_context

EMBED_MODEL = "qwen3-embedding:8b"


@dataclass(frozen=True)
class DatasetSpec:
    key: str
    label: str
    filename_prefixes: tuple[str, ...]


DATASETS: tuple[DatasetSpec, ...] = (
    DatasetSpec("diabetes", "Diabetes", ("MCQs_sample_questions2015_full", "MCQs_book")),
    DatasetSpec("adrenal", "Adrenal", ("AdrenalGlands_dataset", "AdrenalGlands")),
    DatasetSpec("thyroid", "Thyroid", ("ThyroidGland_dataset", "ThyroidGland")),
    DatasetSpec(
        "pituitary",
        "Pituitary",
        ("PituitaryGlandAndHypothalamus_dataset", "PituitaryGlandAndHypothalamus"),
    ),
    DatasetSpec(
        "parathyroid",
        "Parathyroid",
        ("ParathyroidGlandAndBoneDisease_dataset", "ParathyroidGlandAndBoneDisease"),
    ),
    DatasetSpec(
        "reproductive",
        "Reproductive",
        ("ReproductiveEndocrinology_dataset", "ReproductiveEndocrinology"),
    ),
)


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _question_correct(record: dict[str, Any]) -> bool:
    for metric in record.get("metrics", []):
        if metric.get("metric") == "ExactMatch":
            return float(metric.get("score", 0.0)) == 1.0
    actual = str(record.get("actual_output", "")).strip().lower()
    expected = str(record.get("expected_output", "")).strip().lower()
    return bool(actual) and actual == expected


def _accuracy_from_file(path: Path) -> tuple[float | None, int, int]:
    data = _load_json(path)
    results = data.get("results", [])
    if not isinstance(results, list) or not results:
        return None, 0, 0
    total = len(results)
    correct = sum(1 for r in results if _question_correct(r))
    return (correct / total) * 100.0 if total else None, correct, total


def _find_eval_file(llm_dir: str, dataset: DatasetSpec, *, oracle: bool) -> Path | None:
    return get_resolver().find_vector_rag_file(
        llm_dir,
        dataset.filename_prefixes,
        with_rerank=True,
        oracle=oracle,
    )


def _fmt(v: float | None) -> str:
    return "—" if v is None else f"{v:.2f}"


def _build_markdown(rows: list[dict[str, str]]) -> str:
    headers = [
        "LLM",
        "Dataset",
        "Rerank no oracle (%)",
        "Rerank with oracle (%)",
        "Delta (oracle - no oracle)",
    ]
    out = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for row in rows:
        out.append(
            "| "
            + " | ".join(
                [
                    row["llm"],
                    row["dataset"],
                    row["no_oracle_acc"],
                    row["oracle_acc"],
                    row["delta"],
                ]
            )
            + " |"
        )
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build comparison table for single-pass rerank accuracy "
            "(no oracle vs oracle routing)."
        )
    )
    add_paper_analysis_args(parser)
    args = parser.parse_args()
    repo_root, _manifest, output_dir = resolve_analysis_context(args)

    resolver = get_resolver()
    llm_dirs = sorted(
        p.name
        for p in resolver.oracle_root.iterdir()
        if p.is_dir()
    )

    rows: list[dict[str, str]] = []
    csv_rows: list[dict[str, str]] = []
    overall_no_correct = 0
    overall_no_total = 0
    overall_oracle_correct = 0
    overall_oracle_total = 0
    for llm in llm_dirs:
        for dataset in DATASETS:
            no_file = _find_eval_file(llm, dataset, oracle=False)
            oracle_file = _find_eval_file(llm, dataset, oracle=True)

            no_acc, nc, nt = (None, 0, 0) if no_file is None else _accuracy_from_file(no_file)
            oracle_acc, oc, ot = (
                (None, 0, 0) if oracle_file is None else _accuracy_from_file(oracle_file)
            )

            if no_acc is not None:
                overall_no_correct += nc
                overall_no_total += nt
            if oracle_acc is not None:
                overall_oracle_correct += oc
                overall_oracle_total += ot

            delta = None
            if no_acc is not None and oracle_acc is not None:
                delta = oracle_acc - no_acc

            rows.append(
                {
                    "llm": llm,
                    "dataset": dataset.label,
                    "no_oracle_acc": _fmt(no_acc),
                    "oracle_acc": _fmt(oracle_acc),
                    "delta": _fmt(delta),
                }
            )
            csv_rows.append(
                {
                    "llm": llm,
                    "dataset": dataset.label,
                    "no_oracle_acc_pct": "" if no_acc is None else f"{no_acc:.2f}",
                    "oracle_acc_pct": "" if oracle_acc is None else f"{oracle_acc:.2f}",
                    "delta_oracle_minus_no_oracle_pct": ""
                    if delta is None
                    else f"{delta:.2f}",
                }
            )

    overall_no_micro = (
        ((overall_no_correct / overall_no_total) * 100.0) if overall_no_total else None
    )
    overall_oracle_micro = (
        ((overall_oracle_correct / overall_oracle_total) * 100.0)
        if overall_oracle_total
        else None
    )
    overall_delta = (
        None
        if (overall_no_micro is None or overall_oracle_micro is None)
        else (overall_oracle_micro - overall_no_micro)
    )

    rows.append(
        {
            "llm": "ALL",
            "dataset": "Overall micro avg",
            "no_oracle_acc": _fmt(overall_no_micro),
            "oracle_acc": _fmt(overall_oracle_micro),
            "delta": _fmt(overall_delta),
        }
    )
    csv_rows.append(
        {
            "llm": "ALL",
            "dataset": "Overall micro avg",
            "no_oracle_acc_pct": ""
            if overall_no_micro is None
            else f"{overall_no_micro:.2f}",
            "oracle_acc_pct": ""
            if overall_oracle_micro is None
            else f"{overall_oracle_micro:.2f}",
            "delta_oracle_minus_no_oracle_pct": ""
            if overall_delta is None
            else f"{overall_delta:.2f}",
        }
    )

    markdown = _build_markdown(rows)
    print(markdown)

    output_dir.mkdir(parents=True, exist_ok=True)
    md_path = output_dir / "table_rerank_oracle_vs_no_oracle_accuracy.md"
    csv_path = output_dir / "table_rerank_oracle_vs_no_oracle_accuracy.csv"
    md_path.write_text(markdown + "\n", encoding="utf-8")

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "llm",
                "dataset",
                "no_oracle_acc_pct",
                "oracle_acc_pct",
                "delta_oracle_minus_no_oracle_pct",
            ],
        )
        writer.writeheader()
        writer.writerows(csv_rows)

    print(f"\nWrote {md_path.relative_to(repo_root)}")
    print(f"Wrote {csv_path.relative_to(repo_root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
