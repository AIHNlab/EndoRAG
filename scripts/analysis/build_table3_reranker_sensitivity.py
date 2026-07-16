#!/usr/bin/env python3
"""Build Table 3-style reranker sensitivity summary for Cosine_C512_100 + qwen3-embedding:8b.

Compares vector RAG without reranking vs four rerankers across LLM backbones and
seven endocrine MCQ datasets. Reports per-dataset accuracy and macro average
(mean of dataset accuracies), rounded to two decimal places.
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from build_table2_accuracy import (
    DATASETS,
    EMBED_DIR,
    PCT_DIGITS,
    VECTOR_SUBDIR,
    DatasetSpec,
    Highlight,
    _counts_from_results,
    _csv_num,
    _fmt,
    _fmt_latex,
    _format_highlighted_cell,
    _latex_escape,
    _load_json,
    _macro_avg,
    _pct,
    _rank_highlights,
    _round_pct,
)
from endorag.analysis.artifact_resolver import RERANK_PREFIX, get_resolver
from endorag.analysis.cli_args import add_paper_analysis_args, resolve_analysis_context

HighlightKey = tuple[str, str, str]  # (llm_label, reranker_key, column_key)


@dataclass(frozen=True)
class RerankLlmSpec:
    """Filesystem directory per LLM label (with optional per-reranker overrides)."""

    label: str
    default_dir: str
    rerank_dirs: tuple[tuple[str, str], ...] = ()

    def dir_for(self, reranker_key: str) -> str:
        for key, path in self.rerank_dirs:
            if key == reranker_key:
                return path
        return self.default_dir


@dataclass(frozen=True)
class RerankerSpec:
    key: str
    label: str
    filename_prefix: str | None  # None = no rerank baseline


DEFAULT_LLMS: tuple[RerankLlmSpec, ...] = (
    RerankLlmSpec(
        "gemma4:31b-cloud",
        "gemma4:31b-cloud",
        (("qwen8b", "gemma4:31b-cloud"),),
    ),
    RerankLlmSpec("nemotron-3-nano:30b", "nemotron-3-nano:30b-cloud"),
    RerankLlmSpec("mistral-small3.2:24b", "mistral-small3.2:24b"),
    RerankLlmSpec("minimax-m2.7:cloud", "minimax-m2.7:cloud"),
)

RERANKERS: tuple[RerankerSpec, ...] = (
    RerankerSpec("none", "No reranker", None),
    RerankerSpec("qwen8b", "Qwen3-Reranker-8B", "rerank_qwen8b_Qwen_Qwen3-Reranker-8B"),
    RerankerSpec("bge", "bge-reranker-v2-m3", "rerank_bge_BAAI_bge-reranker-v2-m3"),
    RerankerSpec("jina", "jina-reranker-v3", "rerank_jina_jinaai_jina-reranker-v3"),
    RerankerSpec(
        "ms-marco",
        "ms-marco-MiniLM-L-6-v2",
        "rerank_ms-marco_cross-encoder_ms-marco-MiniLM-L-6-v2",
    ),
)


@dataclass
class DatasetMetrics:
    accuracy_pct: float | None = None
    correct: int | None = None
    total: int | None = None
    source_path: str | None = None


@dataclass
class RerankerRow:
    llm: str
    reranker_key: str
    reranker_label: str
    datasets: dict[str, DatasetMetrics] = field(default_factory=dict)
    macro_avg_pct: float | None = None
    micro_avg_pct: float | None = None


def _find_eval_file(
    llm_dir: str,
    dataset: DatasetSpec,
    reranker: RerankerSpec,
) -> Path | None:
    base = get_resolver().vector_base(llm_dir) / VECTOR_SUBDIR
    if not base.is_dir():
        return None

    suffixes = get_resolver().embed_suffixes()
    prefix = reranker.filename_prefix
    matches: list[Path] = []

    for path in base.iterdir():
        name = path.name
        if not name.endswith("_1.json"):
            continue
        if "diabetesVectorTool512_100" not in name:
            continue
        if prefix is None:
            if name.startswith("rerank_"):
                continue
            stem = name
        else:
            if not name.startswith(f"{prefix}_"):
                continue
            stem = name[len(prefix) + 1 :]
        if not any(name.endswith(s) for s in suffixes):
            continue
        if not any(stem.startswith(p) for p in dataset.vector_prefixes):
            continue
        matches.append(path)

    if not matches:
        return None
    matches.sort(key=lambda p: p.name)
    return matches[0]


def _collect_reranker_row(
    llm: RerankLlmSpec,
    reranker: RerankerSpec,
) -> RerankerRow:
    row = RerankerRow(
        llm=llm.label,
        reranker_key=reranker.key,
        reranker_label=reranker.label,
    )
    llm_dir = llm.dir_for(reranker.key)

    acc_values: list[float | None] = []
    pooled_correct = 0
    pooled_total = 0

    for dataset in DATASETS:
        metrics = DatasetMetrics()
        path = _find_eval_file(llm_dir, dataset, reranker)
        if path is None:
            row.datasets[dataset.key] = metrics
            acc_values.append(None)
            continue

        metrics.source_path = get_resolver().relative_to_repo(path)
        data = _load_json(path)
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
        row.datasets[dataset.key] = metrics

    row.macro_avg_pct = _macro_avg(acc_values)
    row.micro_avg_pct = _pct(pooled_correct, pooled_total)
    return row


def build_table(llms: tuple[RerankLlmSpec, ...] | None = None) -> list[RerankerRow]:
    llms = llms or DEFAULT_LLMS
    rows: list[RerankerRow] = []
    for llm in llms:
        for reranker in RERANKERS:
            rows.append(_collect_reranker_row(llm, reranker))
    return rows


def _column_keys() -> tuple[str, ...]:
    return tuple(d.key for d in DATASETS) + ("macro_avg", "micro_avg")


def _cell_value(row: RerankerRow, col: str) -> float | None:
    if col == "macro_avg":
        return row.macro_avg_pct
    if col == "micro_avg":
        return row.micro_avg_pct
    return row.datasets.get(col, DatasetMetrics()).accuracy_pct


def _compute_highlights(rows: list[RerankerRow]) -> dict[HighlightKey, Highlight]:
    highlights: dict[HighlightKey, Highlight] = {}
    llm_labels = list(dict.fromkeys(r.llm for r in rows))
    for llm in llm_labels:
        llm_rows = [r for r in rows if r.llm == llm]
        for col in _column_keys():
            indexed = [
                (i, val)
                for i, row in enumerate(llm_rows)
                if (val := _cell_value(row, col)) is not None
            ]
            for i, kind in _rank_highlights(indexed).items():
                row = llm_rows[i]
                highlights[(row.llm, row.reranker_key, col)] = kind
    return highlights


def _reranker_latex_label(label: str) -> str:
    short = {
        "No reranker": "No reranker",
        "Qwen3-Reranker-8B": r"Qwen3 Reranker-8B",
        "bge-reranker-v2-m3": r"bge-reranker v2-m3",
        "jina-reranker-v3": r"jina-reranker v3",
        "ms-marco-MiniLM-L-6-v2": r"ms-marco MiniLM-L-6-v2",
    }
    return short.get(label, _latex_escape(label))


def _markdown_table(rows: list[RerankerRow], *, highlight: bool) -> str:
    highlights = _compute_highlights(rows) if highlight else {}
    dataset_labels = [d.label for d in DATASETS]

    lines: list[str] = []
    if highlight:
        lines.append(
            "_Best per LLM column: <span style=\"color:red\">red</span>; "
            "second-best: <span style=\"color:blue;text-decoration:underline\">blue underline</span>._"
        )
        lines.append("")

    header = (
        "| LLM | Reranker | "
        + " | ".join(dataset_labels)
        + " | Macro avg | Micro avg |"
    )
    sep = (
        "| --- | --- | "
        + " | ".join(["---"] * len(dataset_labels))
        + " | --- | --- |"
    )
    lines.extend([header, sep])

    for row in rows:
        cells = [row.llm, row.reranker_label]
        for dataset in DATASETS:
            m = row.datasets.get(dataset.key, DatasetMetrics())
            hl = highlights.get((row.llm, row.reranker_key, dataset.key))
            cells.append(
                _format_highlighted_cell(
                    m.accuracy_pct, hl, fmt="markdown" if highlight else "plain"
                )
            )
        macro_hl = highlights.get((row.llm, row.reranker_key, "macro_avg"))
        cells.append(
            _format_highlighted_cell(
                row.macro_avg_pct, macro_hl, fmt="markdown" if highlight else "plain"
            )
        )
        micro_hl = highlights.get((row.llm, row.reranker_key, "micro_avg"))
        cells.append(
            _format_highlighted_cell(
                row.micro_avg_pct, micro_hl, fmt="markdown" if highlight else "plain"
            )
        )
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _latex_table(
    rows: list[RerankerRow],
    *,
    highlight: bool,
    caption: str,
    label: str = "tab:reranker-sensitivity",
) -> str:
    highlights = _compute_highlights(rows) if highlight else {}
    n_data_cols = len(DATASETS) + 2
    col_spec = "l l " + " ".join(["r"] * n_data_cols)

    header_cells = (
        ["LLM", "Reranker"]
        + [d.label for d in DATASETS]
        + [r"Macro avg", r"Micro avg"]
    )
    header = " & ".join(header_cells) + r" \\"

    body_lines: list[str] = []
    llm_labels = list(dict.fromkeys(r.llm for r in rows))
    for llm in llm_labels:
        llm_rows = [r for r in rows if r.llm == llm]
        for row_idx, row in enumerate(llm_rows):
            llm_cell = _latex_escape(llm) if row_idx == 0 else ""
            rerank_cell = _reranker_latex_label(row.reranker_label)
            cells = [llm_cell, rerank_cell]
            for dataset in DATASETS:
                m = row.datasets.get(dataset.key, DatasetMetrics())
                hl = highlights.get((row.llm, row.reranker_key, dataset.key))
                cells.append(
                    _format_highlighted_cell(
                        m.accuracy_pct, hl, fmt="latex" if highlight else "plain"
                    )
                )
            macro_hl = highlights.get((row.llm, row.reranker_key, "macro_avg"))
            cells.append(
                _format_highlighted_cell(
                    row.macro_avg_pct, macro_hl, fmt="latex" if highlight else "plain"
                )
            )
            micro_hl = highlights.get((row.llm, row.reranker_key, "micro_avg"))
            cells.append(
                _format_highlighted_cell(
                    row.micro_avg_pct, micro_hl, fmt="latex" if highlight else "plain"
                )
            )
            body_lines.append(" & ".join(cells) + r" \\")
        if llm != llm_labels[-1]:
            body_lines.append(r"\addlinespace")

    legend = (
        r"\textit{Note:} Best result per LLM column in \textcolor{red}{red}; "
        r"second-best in \textcolor{blue}{\underline{blue}}. "
        r"Macro average = mean of per-dataset accuracies; "
        r"micro average = pooled correct/total (2 d.p.)."
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


def _write_csv(path: Path, rows: list[RerankerRow]) -> None:
    fieldnames = (
        ["llm", "reranker"]
        + [d.key for d in DATASETS]
        + ["macro_avg_pct", "micro_avg_pct"]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            out: dict[str, Any] = {
                "llm": row.llm,
                "reranker": row.reranker_label,
                "macro_avg_pct": _csv_num(row.macro_avg_pct),
                "micro_avg_pct": _csv_num(row.micro_avg_pct),
            }
            for dataset in DATASETS:
                m = row.datasets.get(dataset.key, DatasetMetrics())
                out[dataset.key] = _csv_num(m.accuracy_pct)
            writer.writerow(out)


def _missing_report(rows: list[RerankerRow]) -> str:
    lines = ["## Missing evaluation files", ""]
    any_missing = False
    for row in rows:
        for dataset in DATASETS:
            m = row.datasets.get(dataset.key, DatasetMetrics())
            if m.source_path is None:
                any_missing = True
                lines.append(f"- {row.llm} / {row.reranker_label} / {dataset.label}")
    if not any_missing:
        return ""
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build Table 3 reranker sensitivity summary (Cosine_C512_100, qwen3-embedding:8b)."
    )
    add_paper_analysis_args(parser)
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
    highlight = not args.no_highlight

    caption_plain = (
        "Table 3. Full reranker sensitivity results across LLM backbones and endocrine "
        f"MCQ datasets (Cosine_C512_100, embedding: {EMBED_DIR}). "
        "Macro average = mean of per-dataset accuracies; "
        "micro average = pooled correct/total."
    )
    caption_latex = caption_plain.replace("%", r"\%").replace("_", r"\_")

    md_parts = [
        f"# {caption_plain}",
        "",
        _markdown_table(rows, highlight=highlight),
        "",
    ]
    missing = _missing_report(rows)
    if missing:
        md_parts.append(missing)

    markdown = "\n".join(md_parts)
    print(markdown)

    if args.stdout_only:
        return 0

    output_dir.mkdir(parents=True, exist_ok=True)
    md_path = output_dir / "table3_reranker_sensitivity_cosine_c512_100.md"
    csv_path = output_dir / "table3_reranker_sensitivity_cosine_c512_100.csv"
    tex_path = output_dir / "table3_reranker_sensitivity_cosine_c512_100.tex"

    md_path.write_text(markdown, encoding="utf-8")
    _write_csv(csv_path, rows)

    latex = "\n".join(
        [
            "% Auto-generated by evaluate/build_table3_reranker_sensitivity.py",
            "% Preamble: \\usepackage{xcolor} \\usepackage[normalem]{ulem} \\usepackage{booktabs}",
            "",
            _latex_table(rows, highlight=highlight, caption=caption_latex),
            "",
        ]
    )
    tex_path.write_text(latex, encoding="utf-8")

    print(f"\nWrote {md_path.relative_to(repo_root)}", file=sys.stderr)
    print(f"Wrote {csv_path.relative_to(repo_root)}", file=sys.stderr)
    print(f"Wrote {tex_path.relative_to(repo_root)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
