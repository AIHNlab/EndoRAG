#!/usr/bin/env python3
"""Build literature vs domain-specific comparison table (Qwen3-Reranker-8B).

Loads evaluation JSON files from:
  - Literature:      evaluate/Method_vectorRag/literature/{llm}/qwen3-embedding:8b/LLM/Cosine_C512_100/
  - Domain-specific: evaluate/Method_vectorRag/{llm}/qwen3-embedding:8b/LLM/Cosine_C512_100/

Rows are grouped per LLM: Literature, then Domain-specific.
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from build_table2_accuracy import (
    DATASETS,
    EMBED_DIR,
    PCT_DIGITS,
    VECTOR_SUBDIR,
    DatasetSpec,
    _counts_from_results,
    _load_json,
    _macro_avg,
    _pct,
)
from endorag.analysis.artifact_resolver import RERANK_PREFIX, get_resolver
from endorag.analysis.cli_args import add_paper_analysis_args, resolve_analysis_context


DATASET_COLS: tuple[str, ...] = tuple(d.key for d in DATASETS)

DOMAIN_SPECIFIC_COLS: tuple[str, ...] = tuple(
    d.key for d in DATASETS if d.domain_specific
)

DATASET_LABELS: dict[str, str] = {d.key: d.label for d in DATASETS}


@dataclass(frozen=True)
class LlmSpec:
    """Table label and filesystem directory names."""

    label: str
    domain_dir: str
    literature_dir: str = ""

    @property
    def lit_dir(self) -> str:
        return self.literature_dir or self.domain_dir


DEFAULT_LLMS: tuple[LlmSpec, ...] = (
    LlmSpec("gemma4:31b", "gemma4:31b-cloud"),
    LlmSpec("nemotron-3-nano:30b", "nemotron-3-nano:30b-cloud"),
    LlmSpec("mistral-small3.2:24b", "mistral-small3.2:24b"),
    LlmSpec("minimax-m2.7:cloud", "minimax-m2.7:cloud"),
)

SOURCES: tuple[tuple[str, bool], ...] = (
    ("Literature", True),
    ("Domain-specific", False),
)


@dataclass
class ComparisonRow:
    model: str
    source: str
    values: dict[str, float | None]
    missing: list[str]

    @property
    def macro_avg_pct(self) -> float | None:
        vals = [self.values.get(c) for c in DATASET_COLS if self.values.get(c) is not None]
        return _macro_avg(vals)

    @property
    def domain_macro_avg_pct(self) -> float | None:
        vals = [
            self.values.get(c) for c in DOMAIN_SPECIFIC_COLS if self.values.get(c) is not None
        ]
        return _macro_avg(vals)

    @property
    def micro_avg_pct(self) -> float | None:
        provided = self.values.get("micro_avg")
        if provided is not None:
            return round(provided, PCT_DIGITS)
        return None


def _fmt(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.{PCT_DIGITS}f}"


def _vector_base(llm_dir: str, *, literature: bool) -> Path:
    return get_resolver().vector_base(llm_dir, literature=literature) / VECTOR_SUBDIR


def _find_qwen_rerank_file(
    llm_dir: str,
    dataset: DatasetSpec,
    *,
    literature: bool,
) -> Path | None:
    base = _vector_base(llm_dir, literature=literature)
    if not base.is_dir():
        return None

    resolver = get_resolver()
    suffixes = resolver.embed_suffixes()
    matches: list[Path] = []
    for path in base.iterdir():
        name = path.name
        if not name.startswith(RERANK_PREFIX):
            continue
        if not name.endswith("_1.json"):
            continue
        if "diabetesVectorTool512_100" not in name:
            continue
        if not any(name.endswith(s) for s in suffixes):
            continue
        stem = name[len(RERANK_PREFIX) :]
        if not any(stem.startswith(p) for p in dataset.vector_prefixes):
            continue
        matches.append(path)

    if not matches:
        return None
    matches.sort(key=lambda p: p.name)
    return matches[0]


def _collect_row(llm: LlmSpec, source_label: str, *, literature: bool) -> ComparisonRow:
    llm_dir = llm.lit_dir if literature else llm.domain_dir
    values: dict[str, float | None] = {c: None for c in DATASET_COLS}
    missing: list[str] = []
    acc_values: list[float | None] = []
    pooled_correct = 0
    pooled_total = 0

    for dataset in DATASETS:
        path = _find_qwen_rerank_file(llm_dir, dataset, literature=literature)
        if path is None:
            missing.append(dataset.label)
            acc_values.append(None)
            continue

        data = _load_json(path)
        correct, total, acc_pct = _counts_from_results(data)
        if total > 0:
            values[dataset.key] = acc_pct
            pooled_correct += correct
            pooled_total += total
        else:
            summary_acc = data.get("summary", {}).get("overall_accuracy")
            if summary_acc is not None:
                values[dataset.key] = round(float(summary_acc) * 100.0, PCT_DIGITS)
        acc_values.append(values[dataset.key])

    values["micro_avg"] = _pct(pooled_correct, pooled_total)
    return ComparisonRow(model=llm.label, source=source_label, values=values, missing=missing)


def build_table(llms: tuple[LlmSpec, ...] | None = None) -> list[ComparisonRow]:
    llms = llms or DEFAULT_LLMS
    rows: list[ComparisonRow] = []
    for llm in llms:
        for source_label, literature in SOURCES:
            rows.append(_collect_row(llm, source_label, literature=literature))
    return rows


def _average(values: list[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    if not present:
        return None
    return round(sum(present) / len(present), PCT_DIGITS)


def _append_overall_rows(rows: list[ComparisonRow]) -> list[ComparisonRow]:
    """Append source-level averages across all models."""
    out = list(rows)
    for source_label, _ in SOURCES:
        source_rows = [r for r in rows if r.source == source_label and r.model != "All models"]
        if not source_rows:
            continue
        agg_values: dict[str, float | None] = {}
        for col in DATASET_COLS:
            agg_values[col] = _average([r.values.get(col) for r in source_rows])
        agg_values["micro_avg"] = _average([r.micro_avg_pct for r in source_rows])
        out.append(
            ComparisonRow(
                model="All models",
                source=f"{source_label} (avg)",
                values=agg_values,
                missing=[],
            )
        )
    return out


def _markdown_table(rows: list[ComparisonRow]) -> str:
    header = (
        "| Model | Source | "
        + " | ".join(DATASET_LABELS[c] for c in DATASET_COLS)
        + " | Macro avg | Micro avg |"
    )
    sep = "| --- | --- | " + " | ".join(["---"] * (len(DATASET_COLS) + 2)) + " |"
    lines = [header, sep]
    for row in rows:
        cells = [
            row.model,
            row.source,
            *[_fmt(row.values.get(col)) for col in DATASET_COLS],
            _fmt(row.macro_avg_pct),
            _fmt(row.micro_avg_pct),
        ]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _missing_report(rows: list[ComparisonRow]) -> str:
    lines = ["## Missing evaluation files", ""]
    any_missing = False
    for row in rows:
        if not row.missing:
            continue
        any_missing = True
        for label in row.missing:
            lines.append(f"- {row.model} / {row.source} / {label}")
    if not any_missing:
        return ""
    return "\n".join(lines) + "\n"


def _write_csv(path: Path, rows: list[ComparisonRow]) -> None:
    fields = [
        "model",
        "source",
        *DATASET_COLS,
        "macro_avg_pct",
        "micro_avg_pct",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            out: dict[str, Any] = {
                "model": row.model,
                "source": row.source,
                "macro_avg_pct": _fmt(row.macro_avg_pct) if row.macro_avg_pct is not None else "",
                "micro_avg_pct": _fmt(row.micro_avg_pct) if row.micro_avg_pct is not None else "",
            }
            for col in DATASET_COLS:
                val = row.values.get(col)
                out[col] = _fmt(val) if val is not None else ""
            writer.writerow(out)


def _latex_table(rows: list[ComparisonRow]) -> str:
    header = (
        "Model & Source & Diabetes & Thyroid & Parathyroid & Pituitary & Adrenal & "
        "Reproductive & UKEU & Macro avg & Micro avg \\\\"
    )
    body: list[str] = []
    llm_labels = list(dict.fromkeys(r.model for r in rows))
    for llm in llm_labels:
        llm_rows = [r for r in rows if r.model == llm]
        for row_idx, row in enumerate(llm_rows):
            model_cell = row.model.replace("_", "\\_").replace(":", ":") if row_idx == 0 else ""
            cells = [
                model_cell,
                row.source.replace("_", "\\_"),
                *[_fmt(row.values.get(c)) for c in DATASET_COLS],
                _fmt(row.macro_avg_pct),
                _fmt(row.micro_avg_pct),
            ]
            body.append(" & ".join(cells) + r" \\")
        if llm != llm_labels[-1]:
            body.append(r"\addlinespace")

    caption = (
        r"Literature vs domain-specific vector DB accuracy (\%). "
        r"Cosine\_C512\_100, embedding: qwen3-embedding:8b, reranker: Qwen/Qwen3-Reranker-8B."
    )
    return "\n".join(
        [
            r"\begin{table}[ht]",
            r"\centering",
            rf"\caption{{{caption}}}",
            r"\label{tab:literature-vs-domain-qwen-rerank}",
            r"\begin{tabular}{l l r r r r r r r r r}",
            r"\toprule",
            header,
            r"\midrule",
            *body,
            r"\bottomrule",
            r"\end{tabular}",
            r"\end{table}",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build literature vs domain-specific table (Qwen3-Reranker-8B)."
    )
    add_paper_analysis_args(parser)
    parser.add_argument(
        "--stdout-only",
        action="store_true",
        help="Print markdown to stdout only; do not write files.",
    )
    args = parser.parse_args(argv)
    repo_root, _manifest, output_dir = resolve_analysis_context(args)

    rows = _append_overall_rows(build_table())
    md_parts = [
        "# Literature vs domain-specific (Qwen3-Reranker-8B, Cosine_C512_100)",
        "",
        _markdown_table(rows),
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
    md_path = output_dir / "table_literature_vs_qwen_rerank.md"
    csv_path = output_dir / "table_literature_vs_qwen_rerank.csv"
    tex_path = output_dir / "table_literature_vs_qwen_rerank.tex"

    md_path.write_text(markdown, encoding="utf-8")
    _write_csv(csv_path, rows)
    tex_path.write_text(
        "% Auto-generated by evaluate/build_table_literature_vs_qwen_rerank.py\n\n"
        + _latex_table(rows)
        + "\n",
        encoding="utf-8",
    )

    print(f"\nWrote {md_path.relative_to(repo_root)}", file=sys.stderr)
    print(f"Wrote {csv_path.relative_to(repo_root)}", file=sys.stderr)
    print(f"Wrote {tex_path.relative_to(repo_root)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
