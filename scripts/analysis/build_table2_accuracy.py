#!/usr/bin/env python3
"""Build Table 2-style accuracy summary for Cosine_C512_100 + qwen3-embedding:8b.

Aggregates LLM-only, single-pass vector RAG (with/without Qwen3-Reranker-8B), and
agentic workflow results for configured LLMs. Reports per-dataset
accuracy, macro average (mean of dataset accuracies), micro average (pooled
correct/total), and routing accuracy for agentic runs.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

Highlight = Literal["best", "second"]

from endorag.analysis.artifact_resolver import (
    RERANK_PREFIX,
    VECTOR_SUBDIR,
    EMBED_DIR,
    get_resolver,
)
from endorag.analysis.cli_args import add_paper_analysis_args, resolve_analysis_context

PCT_DIGITS = 2
TIME_DIGITS = 1


@dataclass(frozen=True)
class LlmSpec:
    """Filesystem locations for one paper-table LLM row."""

    label: str
    llm_only_dir: str
    rag_no_rerank_dir: str = ""
    rag_with_rerank_dir: str = ""
    agentic_dir: str = ""

    def dir_for(self, framework_key: str) -> str:
        if framework_key == "llm":
            return self.llm_only_dir
        if framework_key == "rag_no_rerank":
            return self.rag_no_rerank_dir or self.llm_only_dir
        if framework_key == "rag_with_rerank":
            return (
                self.rag_with_rerank_dir
                or self.rag_no_rerank_dir
                or self.llm_only_dir
            )
        if framework_key == "agentic":
            return (
                self.agentic_dir
                or self.rag_with_rerank_dir
                or self.rag_no_rerank_dir
                or self.llm_only_dir
            )
        raise ValueError(f"Unknown framework: {framework_key}")


DEFAULT_LLMS: tuple[LlmSpec, ...] = (
    LlmSpec(
        "gemma4:31b-cloud",
        "gemma4:31b-cloud",
        rag_no_rerank_dir="gemma4:31b-cloud",
        rag_with_rerank_dir="gemma4:31b-cloud",
        agentic_dir="gemma4:31b-cloud",
    ),
    LlmSpec("nemotron-3-nano:30b", "nemotron-3-nano:30b-cloud"),
    LlmSpec("mistral-small3.2:24b", "mistral-small3.2:24b"),
    LlmSpec("minimax-m2.7:cloud", "minimax-m2.7:cloud"),
)

# key, short column label, vector-rag filename prefixes, LLM-only slug, agentic slug,
# source dataset json basename, routing oracle map (None = pinned), is_domain_specific
@dataclass(frozen=True)
class DatasetSpec:
    key: str
    label: str
    vector_prefixes: tuple[str, ...]
    llm_slug: str
    agentic_slug: str
    dataset_file: str
    oracle_map: str | None
    domain_specific: bool


DATASETS: tuple[DatasetSpec, ...] = (
    DatasetSpec(
        "diabetes",
        "Diabetes",
        ("MCQs_book", "MCQs_sample_questions2015_full"),
        "MCQs_book",
        "Diabetes",
        "MCQs_sample_questions2015_full.json",
        "MCQ_question_category_map.json",
        True,
    ),
    DatasetSpec(
        "thyroid",
        "Thyroid",
        ("ThyroidGland_dataset", "ThyroidGland"),
        "ThyroidGland",
        "ThyroidGland",
        "ThyroidGland_dataset.json",
        None,
        True,
    ),
    DatasetSpec(
        "parathyroid",
        "Parathyroid",
        ("ParathyroidGlandAndBoneDisease_dataset", "ParathyroidGlandAndBoneDisease"),
        "ParathyroidGlandAndBoneDisease",
        "ParathyroidGlandAndBoneDisease",
        "ParathyroidGlandAndBoneDisease_dataset.json",
        None,
        True,
    ),
    DatasetSpec(
        "pituitary",
        "Pituitary",
        ("PituitaryGlandAndHypothalamus_dataset", "PituitaryGlandAndHypothalamus"),
        "PituitaryGlandAndHypothalamus",
        "PituitaryGlandAndHypothalamus",
        "PituitaryGlandAndHypothalamus_dataset.json",
        None,
        True,
    ),
    DatasetSpec(
        "adrenal",
        "Adrenal",
        ("AdrenalGlands_dataset", "AdrenalGlands"),
        "AdrenalGlands",
        "AdrenalGlands",
        "AdrenalGlands_dataset.json",
        None,
        True,
    ),
    DatasetSpec(
        "reproductive",
        "Reproductive",
        ("ReproductiveEndocrinology_dataset", "ReproductiveEndocrinology"),
        "ReproductiveEndocrinology",
        "ReproductiveEndocrinology",
        "ReproductiveEndocrinology_dataset.json",
        None,
        True,
    ),
    DatasetSpec(
        "ukeu",
        "UKEU",
        ("UKEU", "UKEU_dataset"),
        "UKEU",
        "UKEU",
        "UKEU.json",
        "UKEU_question_category_map.json",
        False,
    ),
)

# Datasets included in routing accuracy aggregates and the routing table (UKEU excluded).
ROUTING_DATASETS: tuple[DatasetSpec, ...] = tuple(
    d for d in DATASETS if d.key != "ukeu"
)

PINNED_ROUTING: dict[str, str] = {
    "ThyroidGland_dataset.json": "Thyroid Gland",
    "AdrenalGlands_dataset.json": "Adrenal Glands",
    "ParathyroidGlandAndBoneDisease_dataset.json": "Parathyroid Gland and Bone Disease",
    "PituitaryGlandAndHypothalamus_dataset.json": "Pituitary Gland and Hypothalamus",
    "ReproductiveEndocrinology_dataset.json": (
        "Reproductive Endocrinology, Andrology and Sexual Function"
    ),
}

FRAMEWORKS: tuple[tuple[str, str], ...] = (
    ("llm", "LLM"),
    ("rag_no_rerank", "Single-pass RAG w/o rerank"),
    ("rag_with_rerank", "Single-pass RAG w/ rerank"),
    ("agentic", "Agentic Workflow"),
)


@dataclass
class DatasetMetrics:
    accuracy_pct: float | None = None
    correct: int | None = None
    total: int | None = None
    routing_accuracy_pct: float | None = None
    routing_correct: int | None = None
    routing_total: int | None = None
    source_path: str | None = None


@dataclass
class FrameworkRow:
    llm: str
    framework_key: str
    framework_label: str
    datasets: dict[str, DatasetMetrics] = field(default_factory=dict)
    macro_avg_pct: float | None = None
    micro_avg_pct: float | None = None
    execution_median_s: float | None = None
    domain_routing_macro_avg_pct: float | None = None
    domain_routing_micro_avg_pct: float | None = None


def _slug_name(name: str) -> str:
    return name.replace("/", "_").replace(":", "_")


def _embed_suffixes() -> tuple[str, ...]:
    slug = _slug_name(EMBED_DIR)
    return (f"_{EMBED_DIR}_1.json", f"_{slug}_1.json")


def _load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _question_correct(record: dict[str, Any]) -> bool:
    for metric in record.get("metrics", []):
        if metric.get("metric") == "ExactMatch":
            return float(metric.get("score", 0)) == 1.0
    actual = str(record.get("actual_output", "")).strip().lower()
    expected = str(record.get("expected_output", "")).strip().lower()
    return bool(actual) and actual == expected


def _counts_from_results(data: dict[str, Any]) -> tuple[int, int, float]:
    results = data.get("results", [])
    if not results:
        acc = data.get("summary", {}).get("overall_accuracy")
        if acc is not None:
            return 0, 0, _round_pct(float(acc) * 100.0) or 0.0
        return 0, 0, float("nan")

    correct = sum(1 for r in results if _question_correct(r))
    total = len(results)
    acc = _round_pct(correct / total * 100.0) if total else float("nan")
    return correct, total, acc if acc is not None else float("nan")


def _execution_times(data: dict[str, Any]) -> list[float]:
    raw_times = data.get("summary", {}).get("timing", {}).get("per_question_seconds")
    if not isinstance(raw_times, list):
        return []
    times: list[float] = []
    for value in raw_times:
        try:
            times.append(float(value))
        except (TypeError, ValueError):
            continue
    return times


def _load_oracle_map(filename: str | None) -> dict[str, str]:
    return get_resolver().load_oracle_map(filename)


def _expected_routing_category(
    record: dict[str, Any],
    dataset: DatasetSpec,
    oracle_map: dict[str, str],
) -> str | None:
    pinned = PINNED_ROUTING.get(dataset.dataset_file)
    if pinned:
        return pinned
    question = str(record.get("input", "")).strip()
    return oracle_map.get(question)


def _routing_counts(
    data: dict[str, Any],
    dataset: DatasetSpec,
    oracle_map: dict[str, str],
) -> tuple[int, int]:
    correct = 0
    total = 0
    for record in data.get("results", []):
        routed = (record.get("flow_diagnostics") or {}).get("routed_category")
        if not routed:
            continue
        expected = _expected_routing_category(record, dataset, oracle_map)
        if expected is None:
            continue
        total += 1
        if routed == expected:
            correct += 1
    return correct, total


def _find_vector_rag_file(
    llm_dir: str,
    dataset: DatasetSpec,
    *,
    with_rerank: bool,
) -> Path | None:
    resolver = get_resolver()
    return resolver.find_vector_rag_file(
        llm_dir, dataset.vector_prefixes, with_rerank=with_rerank
    )


def _find_llm_only_file(llm_dir: str, dataset: DatasetSpec) -> Path | None:
    return get_resolver().find_llm_only_file(llm_dir, dataset.llm_slug)


def _find_agentic_file(llm_dir: str, dataset: DatasetSpec) -> Path | None:
    return get_resolver().find_agentic_file(llm_dir, dataset.agentic_slug)


def _resolve_eval_path(
    llm_dir: str,
    dataset: DatasetSpec,
    framework_key: str,
) -> Path | None:
    if framework_key == "llm":
        return _find_llm_only_file(llm_dir, dataset)
    if framework_key == "rag_no_rerank":
        return _find_vector_rag_file(llm_dir, dataset, with_rerank=False)
    if framework_key == "rag_with_rerank":
        return _find_vector_rag_file(llm_dir, dataset, with_rerank=True)
    if framework_key == "agentic":
        return _find_agentic_file(llm_dir, dataset)
    raise ValueError(f"Unknown framework: {framework_key}")


def _round_pct(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value, PCT_DIGITS)


def _pct(correct: int, total: int) -> float | None:
    if total <= 0:
        return None
    return _round_pct(correct / total * 100.0)


def _macro_avg(values: list[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    if not present:
        return None
    return _round_pct(sum(present) / len(present))


def _collect_framework_row(
    llm: LlmSpec,
    framework_key: str,
    framework_label: str,
) -> FrameworkRow:
    row = FrameworkRow(
        llm=llm.label,
        framework_key=framework_key,
        framework_label=framework_label,
    )

    acc_values: list[float | None] = []
    pooled_correct = 0
    pooled_total = 0
    execution_times: list[float] = []

    routing_values: list[float | None] = []
    routing_pooled_correct = 0
    routing_pooled_total = 0

    for dataset in DATASETS:
        metrics = DatasetMetrics()
        path = _resolve_eval_path(llm.dir_for(framework_key), dataset, framework_key)
        if path is None:
            row.datasets[dataset.key] = metrics
            acc_values.append(None)
            continue

        metrics.source_path = get_resolver().relative_to_repo(path)
        data = _load_json(path)
        execution_times.extend(_execution_times(data))
        correct, total, acc_pct = _counts_from_results(data)
        if total > 0:
            metrics.correct = correct
            metrics.total = total
            metrics.accuracy_pct = acc_pct
            pooled_correct += correct
            pooled_total += total
        else:
            summary_acc = data.get("summary", {}).get("overall_accuracy")
            if summary_acc is not None:
                metrics.accuracy_pct = _round_pct(float(summary_acc) * 100.0)

        acc_values.append(metrics.accuracy_pct)

        if framework_key == "agentic":
            oracle_map = _load_oracle_map(dataset.oracle_map)
            r_correct, r_total = _routing_counts(data, dataset, oracle_map)
            if r_total > 0:
                metrics.routing_correct = r_correct
                metrics.routing_total = r_total
                metrics.routing_accuracy_pct = _pct(r_correct, r_total)
                if dataset in ROUTING_DATASETS:
                    routing_values.append(metrics.routing_accuracy_pct)
                    routing_pooled_correct += r_correct
                    routing_pooled_total += r_total

        row.datasets[dataset.key] = metrics

    row.macro_avg_pct = _macro_avg(acc_values)
    row.micro_avg_pct = _pct(pooled_correct, pooled_total)
    if execution_times:
        row.execution_median_s = round(statistics.median(execution_times), TIME_DIGITS)
    row.domain_routing_macro_avg_pct = _macro_avg(routing_values)
    row.domain_routing_micro_avg_pct = _pct(routing_pooled_correct, routing_pooled_total)
    return row


def build_table(llms: tuple[LlmSpec, ...] | None = None) -> list[FrameworkRow]:
    llms = llms or DEFAULT_LLMS
    rows: list[FrameworkRow] = []
    for llm in llms:
        for framework_key, framework_label in FRAMEWORKS:
            rows.append(_collect_framework_row(llm, framework_key, framework_label))
    return rows


def _fmt(value: float | None, digits: int = PCT_DIGITS) -> str:
    if value is None:
        return "—"
    return f"{value:.{digits}f}"


def _fmt_latex(value: float | None, digits: int = PCT_DIGITS) -> str:
    if value is None:
        return "---"
    return f"{value:.{digits}f}"


def _csv_num(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:.{PCT_DIGITS}f}"


def _fmt_time(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.{TIME_DIGITS}f}"


def _fmt_time_latex(value: float | None) -> str:
    if value is None:
        return "---"
    return f"{value:.{TIME_DIGITS}f}"


def _accuracy_column_keys() -> tuple[str, ...]:
    return tuple(d.key for d in DATASETS) + ("macro_avg", "micro_avg")


def _cell_value(row: FrameworkRow, col: str) -> float | None:
    if col == "macro_avg":
        return row.macro_avg_pct
    if col == "micro_avg":
        return row.micro_avg_pct
    m = row.datasets.get(col, DatasetMetrics())
    return m.accuracy_pct


def _rank_highlights(
    indexed_values: list[tuple[int, float]],
) -> dict[int, Highlight]:
    """Best = highest; second = second-highest distinct value (ties included)."""
    if not indexed_values:
        return {}
    distinct = sorted({v for _, v in indexed_values}, reverse=True)
    best_val = distinct[0]
    second_val = distinct[1] if len(distinct) > 1 else None
    out: dict[int, Highlight] = {}
    for idx, val in indexed_values:
        if val == best_val:
            out[idx] = "best"
        elif second_val is not None and val == second_val:
            out[idx] = "second"
    return out


def _compute_accuracy_highlights(
    rows: list[FrameworkRow],
) -> dict[tuple[str, str, str], Highlight]:
    """Per-LLM, per accuracy column: mark best (red) and second-best (blue underline)."""
    highlights: dict[tuple[str, str, str], Highlight] = {}
    llm_labels = list(dict.fromkeys(r.llm for r in rows))
    for llm in llm_labels:
        llm_rows = [r for r in rows if r.llm == llm]
        for col in _accuracy_column_keys():
            indexed = [
                (i, val)
                for i, row in enumerate(llm_rows)
                if (val := _cell_value(row, col)) is not None
            ]
            for i, kind in _rank_highlights(indexed).items():
                row = llm_rows[i]
                highlights[(row.llm, row.framework_key, col)] = kind
    return highlights


def _format_highlighted_cell(
    value: float | None,
    highlight: Highlight | None,
    *,
    fmt: Literal["plain", "markdown", "latex"],
    digits: int = PCT_DIGITS,
    bold_best: bool = True,
) -> str:
    text = _fmt(value, digits=digits)
    if value is None or highlight is None:
        if fmt == "latex":
            return _fmt_latex(value, digits=digits)
        return text
    if text == "—":
        return _fmt_latex(value, digits=digits) if fmt == "latex" else text
    if fmt == "plain":
        return text
    if fmt == "markdown":
        if highlight == "best":
            weight = "bold" if bold_best else "600"
            return f'<span style="color:red;font-weight:{weight}">{text}</span>'
        return f'<span style="color:blue;text-decoration:underline">{text}</span>'
    # latex
    escaped = text.replace("%", r"\%")
    if highlight == "best":
        if bold_best:
            return rf"\textbf{{\textcolor{{red}}{{{escaped}}}}}"
        return rf"\textcolor{{red}}{{{escaped}}}"
    return rf"\textcolor{{blue}}{{\underline{{{escaped}}}}}"


def _latex_escape(text: str) -> str:
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(replacements.get(ch, ch) for ch in text)


def _framework_latex_label(label: str) -> str:
    short = {
        "LLM": "LLM",
        "Single-pass RAG w/o rerank": r"Single-pass RAG w/o rerank",
        "Single-pass RAG w/ rerank": r"Single-pass RAG w/ rerank",
        "Agentic Workflow": r"Agentic Workflow",
    }
    return short.get(label, _latex_escape(label))


def _markdown_table(
    rows: list[FrameworkRow],
    include_routing: bool,
    *,
    highlight: bool = True,
) -> str:
    header_cells = (
        ["LLM", "Framework"]
        + [d.label for d in DATASETS]
        + ["Macro avg", "Micro avg"]
    )
    if include_routing:
        header_cells += ["Domain routing macro", "Domain routing micro"]
    header_cells.append("Median time (s)")
    header = "| " + " | ".join(header_cells) + " |\n"
    header += "| " + " | ".join(["---"] * len(header_cells)) + " |\n"

    highlights = _compute_accuracy_highlights(rows) if highlight else {}

    lines: list[str] = []
    if highlight:
        lines.append(
            "_Best per LLM column: <span style=\"color:red;font-weight:bold\">red bold</span>; "
            "second-best: <span style=\"color:blue;text-decoration:underline\">blue underline</span>._"
        )
        lines.append("")
    lines.append(header.rstrip("\n"))
    for row in rows:
        cells = [
            row.llm,
            row.framework_label,
        ]
        for dataset in DATASETS:
            m = row.datasets.get(dataset.key, DatasetMetrics())
            hl = highlights.get((row.llm, row.framework_key, dataset.key))
            cells.append(
                _format_highlighted_cell(
                    m.accuracy_pct, hl, fmt="markdown" if highlight else "plain"
                )
            )
        macro_hl = highlights.get((row.llm, row.framework_key, "macro_avg"))
        cells.append(
            _format_highlighted_cell(
                row.macro_avg_pct, macro_hl, fmt="markdown" if highlight else "plain"
            )
        )
        micro_hl = highlights.get((row.llm, row.framework_key, "micro_avg"))
        cells.append(
            _format_highlighted_cell(
                row.micro_avg_pct, micro_hl, fmt="markdown" if highlight else "plain"
            )
        )
        if include_routing:
            if row.framework_key == "agentic":
                cells.extend(
                    [
                        _fmt(row.domain_routing_macro_avg_pct),
                        _fmt(row.domain_routing_micro_avg_pct),
                    ]
                )
            else:
                cells.extend(["—", "—"])
        cells.append(_fmt_time(row.execution_median_s))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _latex_table(
    rows: list[FrameworkRow],
    *,
    highlight: bool = True,
    caption: str,
    label: str = "tab:accuracy-table2",
) -> str:
    highlights = _compute_accuracy_highlights(rows) if highlight else {}
    n_data_cols = len(DATASETS) + 3  # macro + micro + median execution time
    col_spec = "l l " + " ".join(["r"] * n_data_cols)

    header_cells = (
        ["LLM", "Framework"]
        + [d.label for d in DATASETS]
        + [r"Macro avg", r"Micro avg", r"Median time (s)"]
    )
    header = " & ".join(header_cells) + r" \\"

    body_lines: list[str] = []
    llm_labels = list(dict.fromkeys(r.llm for r in rows))
    for llm in llm_labels:
        llm_rows = [r for r in rows if r.llm == llm]
        for row_idx, row in enumerate(llm_rows):
            llm_cell = _latex_escape(llm) if row_idx == 0 else ""
            fw_cell = _framework_latex_label(row.framework_label)
            cells = [llm_cell, fw_cell]
            for dataset in DATASETS:
                m = row.datasets.get(dataset.key, DatasetMetrics())
                hl = highlights.get((row.llm, row.framework_key, dataset.key))
                cells.append(
                    _format_highlighted_cell(
                        m.accuracy_pct, hl, fmt="latex" if highlight else "plain"
                    )
                )
            macro_hl = highlights.get((row.llm, row.framework_key, "macro_avg"))
            cells.append(
                _format_highlighted_cell(
                    row.macro_avg_pct, macro_hl, fmt="latex" if highlight else "plain"
                )
            )
            micro_hl = highlights.get((row.llm, row.framework_key, "micro_avg"))
            cells.append(
                _format_highlighted_cell(
                    row.micro_avg_pct, micro_hl, fmt="latex" if highlight else "plain"
                )
            )
            cells.append(_fmt_time_latex(row.execution_median_s))
            body_lines.append(" & ".join(cells) + r" \\")
        if llm != llm_labels[-1]:
            body_lines.append(r"\addlinespace")

    legend = (
        r"\textit{Note:} Best result per LLM column in \textbf{\textcolor{red}{red bold}}; "
        r"second-best in \textcolor{blue}{\underline{blue}}. "
        r"Macro average = mean of per-dataset accuracies; micro average = pooled correct/total. "
        r"Median time is computed over all available per-question execution times."
    )

    return "\n".join(
        [
            r"\begin{table}[ht]",
            r"\centering",
            rf"\caption{{{caption}}}",
            rf"\label{{{label}}}",
            rf"\begin{{tabular}}{{{col_spec}}}",
            r"\toprule",
            header,
            r"\midrule",
            *body_lines,
            r"\bottomrule",
            r"\end{tabular}",
            r"\par\smallskip",
            legend,
            r"\end{table}",
        ]
    )


def _latex_routing_table(
    rows: list[FrameworkRow],
    caption: str,
    label: str = "tab:routing-accuracy",
) -> str:
    agentic_rows = [
        r
        for r in rows
        if r.framework_key == "agentic" and r.domain_routing_micro_avg_pct is not None
    ]
    if not agentic_rows:
        return ""

    n_cols = len(ROUTING_DATASETS) + 2
    col_spec = "l " + " ".join(["r"] * n_cols)
    header = (
        ["LLM"]
        + [d.label for d in ROUTING_DATASETS]
        + [
            r"\shortstack{Domain\\macro}",
            r"\shortstack{Domain\\micro}",
        ]
    )
    body: list[str] = []
    for row in agentic_rows:
        cells = [_latex_escape(row.llm)]
        for dataset in ROUTING_DATASETS:
            m = row.datasets.get(dataset.key, DatasetMetrics())
            cells.append(_fmt_latex(m.routing_accuracy_pct))
        cells.extend(
            [
                _fmt_latex(row.domain_routing_macro_avg_pct),
                _fmt_latex(row.domain_routing_micro_avg_pct),
            ]
        )
        body.append(" & ".join(cells) + r" \\")

    return "\n".join(
        [
            r"\begin{table}[ht]",
            r"\centering",
            rf"\caption{{{caption}}}",
            rf"\label{{{label}}}",
            rf"\begin{{tabular}}{{{col_spec}}}",
            r"\toprule",
            " & ".join(header) + r" \\",
            r"\midrule",
            *body,
            r"\bottomrule",
            r"\end{tabular}",
            r"\end{table}",
        ]
    )


def _write_csv(path: Path, rows: list[FrameworkRow], include_routing: bool) -> None:
    fieldnames = (
        ["llm", "framework"]
        + [d.key for d in DATASETS]
        + ["macro_avg_pct", "micro_avg_pct"]
    )
    if include_routing:
        fieldnames += [
            "domain_routing_macro_avg_pct",
            "domain_routing_micro_avg_pct",
        ]
        for d in DATASETS:
            fieldnames.append(f"routing_{d.key}_pct")
    fieldnames.append("median_time_s")

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            out: dict[str, Any] = {
                "llm": row.llm,
                "framework": row.framework_label,
                "macro_avg_pct": _csv_num(row.macro_avg_pct),
                "micro_avg_pct": _csv_num(row.micro_avg_pct),
                "median_time_s": _fmt_time(row.execution_median_s)
                if row.execution_median_s is not None
                else "",
            }
            for dataset in DATASETS:
                m = row.datasets.get(dataset.key, DatasetMetrics())
                out[dataset.key] = _csv_num(m.accuracy_pct)
            if include_routing:
                out.update(
                    {
                        "domain_routing_macro_avg_pct": _csv_num(
                            row.domain_routing_macro_avg_pct
                        ),
                        "domain_routing_micro_avg_pct": _csv_num(
                            row.domain_routing_micro_avg_pct
                        ),
                    }
                )
                for dataset in DATASETS:
                    m = row.datasets.get(dataset.key, DatasetMetrics())
                    out[f"routing_{dataset.key}_pct"] = _csv_num(
                        m.routing_accuracy_pct
                    )
            writer.writerow(out)


def _routing_markdown(rows: list[FrameworkRow]) -> str:
    """Per-dataset routing accuracy for agentic workflow only."""
    agentic_rows = [
        r
        for r in rows
        if r.framework_key == "agentic" and r.domain_routing_micro_avg_pct is not None
    ]
    if not agentic_rows:
        return "_No agentic workflow evaluation files found._"

    header = (
        "| LLM | "
        + " | ".join(d.label for d in ROUTING_DATASETS)
        + " | Domain macro | Domain micro |\n"
    )
    header += "| --- | " + " | ".join(["---"] * (len(ROUTING_DATASETS) + 2)) + " |\n"

    lines = [header.rstrip()]
    for row in agentic_rows:
        cells = [row.llm]
        for dataset in ROUTING_DATASETS:
            m = row.datasets.get(dataset.key, DatasetMetrics())
            cells.append(_fmt(m.routing_accuracy_pct))
        cells.extend(
            [
                _fmt(row.domain_routing_macro_avg_pct),
                _fmt(row.domain_routing_micro_avg_pct),
            ]
        )
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _missing_report(rows: list[FrameworkRow]) -> str:
    lines = ["## Missing evaluation files", ""]
    any_missing = False
    for row in rows:
        for dataset in DATASETS:
            m = row.datasets.get(dataset.key, DatasetMetrics())
            if m.source_path is None:
                any_missing = True
                lines.append(
                    f"- {row.llm} / {row.framework_label} / {dataset.label}"
                )
    if not any_missing:
        return ""
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build Table 2-style accuracy summary (Cosine_C512_100, qwen3-embedding:8b)."
    )
    add_paper_analysis_args(parser)
    parser.add_argument(
        "--no-routing",
        action="store_true",
        help="Omit routing accuracy columns (agentic only).",
    )
    parser.add_argument(
        "--stdout-only",
        action="store_true",
        help="Print markdown to stdout only; do not write files.",
    )
    parser.add_argument(
        "--no-highlight",
        action="store_true",
        help="Disable best/second-best highlighting in markdown and LaTeX.",
    )
    args = parser.parse_args(argv)
    repo_root, _manifest, output_dir = resolve_analysis_context(args)

    rows = build_table()
    include_routing = not args.no_routing
    highlight = not args.no_highlight

    caption = (
        "Table 2. Accuracy (\\%) on seven endocrine MCQ datasets "
        f"(Cosine\\_C512\\_100, embedding: {EMBED_DIR}; reranker: Qwen/Qwen3-Reranker-8B)."
    )
    caption_plain = caption.replace(r"\%", "%").replace(r"\_", "_")

    md_parts = [
        f"# {caption_plain} Macro average = mean of per-dataset accuracies; "
        "micro average = pooled correct/total.",
        "",
        _markdown_table(
            rows, include_routing=include_routing, highlight=highlight
        ),
        "",
    ]
    if include_routing:
        md_parts.extend(
            [
                "## Agentic routing accuracy (%)",
                "",
                "Routing compares `flow_diagnostics.routed_category` to the pinned category "
                "on monotopic datasets or the oracle question→category map on Diabetes "
                "(UKEU excluded). Domain macro = mean of per-dataset routing accuracies; "
                "domain micro = pooled correct/total.",
                "",
                _routing_markdown(rows),
                "",
            ]
        )

    missing = _missing_report(rows)
    if missing:
        md_parts.extend([missing])

    markdown = "\n".join(md_parts)
    print(markdown)

    if args.stdout_only:
        return 0

    output_dir.mkdir(parents=True, exist_ok=True)
    md_path = output_dir / "table2_accuracy_cosine_c512_100.md"
    csv_path = output_dir / "table2_accuracy_cosine_c512_100.csv"
    tex_path = output_dir / "table2_accuracy_cosine_c512_100.tex"
    md_path.write_text(markdown, encoding="utf-8")
    _write_csv(csv_path, rows, include_routing=include_routing)

    latex_parts = [
        "% Auto-generated by evaluate/build_table2_accuracy.py",
        "% Preamble (add to your .tex file if not already present):",
        "% \\usepackage{xcolor}",
        "% \\usepackage[normalem]{ulem}",
        "% \\usepackage{booktabs}",
        "",
        _latex_table(rows, highlight=highlight, caption=caption),
        "",
    ]
    if include_routing:
        routing_caption = (
            "Agentic workflow routing accuracy (\\%). "
            "Compared to pinned category on monotopic datasets or oracle map on Diabetes; "
            "UKEU excluded. Domain macro = mean of per-dataset routing accuracies; "
            "domain micro = pooled correct/total."
        )
        routing_tex = _latex_routing_table(rows, caption=routing_caption)
        if routing_tex:
            latex_parts.extend([routing_tex, ""])
    tex_path.write_text("\n".join(latex_parts), encoding="utf-8")

    print(f"\nWrote {md_path.relative_to(repo_root)}", file=sys.stderr)
    print(f"Wrote {csv_path.relative_to(repo_root)}", file=sys.stderr)
    print(f"Wrote {tex_path.relative_to(repo_root)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
