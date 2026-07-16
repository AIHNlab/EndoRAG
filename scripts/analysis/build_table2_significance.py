#!/usr/bin/env python3
"""Compute paired significance tests for Table 2 accuracy results.

The Table 2 rows evaluate the same questions across methods for each LLM and
dataset. This script uses those raw per-question correctness vectors to run
exact McNemar tests for every pair of methods within each LLM/dataset block.
"""

from __future__ import annotations

import argparse
import csv
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scipy import stats

from build_table2_accuracy import (
    DATASETS,
    DEFAULT_LLMS,
    FRAMEWORKS,
    _load_json,
    _question_correct,
    _resolve_eval_path,
)
from endorag.analysis.artifact_resolver import get_resolver
from endorag.analysis.cli_args import add_paper_analysis_args, resolve_analysis_context

ALPHA = 0.05
EXPORT_STEM = "table2_significance_cosine_c512_100"
EXCLUDED_LLMS = {"qwen3:30b"}
PLANNED_METHOD_PAIR = ("rag_with_rerank", "agentic")


@dataclass(frozen=True)
class MethodResults:
    key: str
    label: str
    source_path: Path
    question_keys: tuple[str, ...]
    correctness: dict[str, bool]

    @property
    def correct(self) -> int:
        return sum(self.correctness.values())

    @property
    def total(self) -> int:
        return len(self.correctness)

    @property
    def accuracy_pct(self) -> float:
        if not self.total:
            return float("nan")
        return self.correct / self.total * 100.0


@dataclass(frozen=True)
class PairwiseResult:
    llm: str
    dataset: str
    method_a: str
    method_b: str
    paired_n: int
    method_a_paired_correct: int
    method_b_paired_correct: int
    method_a_total: int
    method_b_total: int
    method_a_source_accuracy_pct: float
    method_b_source_accuracy_pct: float
    paired_delta_accuracy_pct: float
    a_correct_b_wrong: int
    a_wrong_b_correct: int
    mcnemar_p_value: float
    mcnemar_significant_0_05: bool
    paired_t_statistic: float
    paired_t_p_value: float
    paired_t_significant_0_05: bool
    source_a: str
    source_b: str


def _question_key(record: dict[str, Any]) -> str:
    return str(record.get("input", "")).strip()


def _load_method_results(
    llm_dir: str,
    framework_key: str,
    framework_label: str,
    dataset: Any,
) -> MethodResults | None:
    path = _resolve_eval_path(llm_dir, dataset, framework_key)
    if path is None:
        return None

    data = _load_json(path)
    question_keys: list[str] = []
    correctness: dict[str, bool] = {}
    for index, record in enumerate(data.get("results", [])):
        key = _question_key(record) or f"__row_{index}"
        if key in correctness:
            raise ValueError(f"Duplicate question key in {path}: {key[:80]}")
        question_keys.append(key)
        correctness[key] = _question_correct(record)

    if not correctness:
        return None

    return MethodResults(
        key=framework_key,
        label=framework_label,
        source_path=path,
        question_keys=tuple(question_keys),
        correctness=correctness,
    )


def _exact_mcnemar_p_value(a_correct_b_wrong: int, a_wrong_b_correct: int) -> float:
    """Two-sided exact McNemar p-value via the binomial distribution."""
    discordant = a_correct_b_wrong + a_wrong_b_correct
    if discordant == 0:
        return 1.0

    smaller = min(a_correct_b_wrong, a_wrong_b_correct)
    tail = sum(math.comb(discordant, k) for k in range(smaller + 1))
    return min(1.0, 2.0 * tail / (2**discordant))


def _paired_counts(
    method_a: MethodResults,
    method_b: MethodResults,
) -> tuple[int, int, int, int, int, list[int]]:
    keys_a = set(method_a.correctness)
    keys_b = set(method_b.correctness)
    if keys_a == keys_b:
        paired = [
            (method_a.correctness[key], method_b.correctness[key])
            for key in method_a.question_keys
        ]
    elif len(method_a.question_keys) == len(method_b.question_keys):
        position_matches = sum(
            key_a == key_b
            for key_a, key_b in zip(
                method_a.question_keys,
                method_b.question_keys,
                strict=True,
            )
        )
        match_ratio = position_matches / len(method_a.question_keys)
        if match_ratio < 0.95:
            only_a = len(keys_a - keys_b)
            only_b = len(keys_b - keys_a)
            raise ValueError(
                "Question sets do not match for "
                f"{method_a.label} vs {method_b.label}: "
                f"position_match_ratio={match_ratio:.3f}, "
                f"only_a={only_a}, only_b={only_b}"
            )
        paired = [
            (method_a.correctness[key_a], method_b.correctness[key_b])
            for key_a, key_b in zip(
                method_a.question_keys,
                method_b.question_keys,
                strict=True,
            )
        ]
    else:
        common_keys = [key for key in method_a.question_keys if key in keys_b]
        if not common_keys:
            only_a = len(keys_a - keys_b)
            only_b = len(keys_b - keys_a)
            raise ValueError(
                "Question counts do not match for "
                f"{method_a.label} vs {method_b.label}: "
                f"n_a={len(method_a.question_keys)}, "
                f"n_b={len(method_b.question_keys)}, "
                f"only_a={only_a}, only_b={only_b}"
            )
        paired = [
            (method_a.correctness[key], method_b.correctness[key])
            for key in common_keys
        ]

    a_correct_b_wrong = 0
    a_wrong_b_correct = 0
    method_a_paired_correct = 0
    method_b_paired_correct = 0
    differences: list[int] = []
    for a_correct, b_correct in paired:
        method_a_paired_correct += int(a_correct)
        method_b_paired_correct += int(b_correct)
        differences.append(int(b_correct) - int(a_correct))
        if a_correct and not b_correct:
            a_correct_b_wrong += 1
        elif not a_correct and b_correct:
            a_wrong_b_correct += 1

    return (
        len(paired),
        method_a_paired_correct,
        method_b_paired_correct,
        a_correct_b_wrong,
        a_wrong_b_correct,
        differences,
    )


def _paired_t_test(differences: list[int]) -> tuple[float, float]:
    """Two-sided paired t-test on per-question correctness differences."""
    if not differences or all(diff == 0 for diff in differences):
        return 0.0, 1.0
    result = stats.ttest_1samp(differences, popmean=0.0)
    return float(result.statistic), float(result.pvalue)


def calculate_pairwise_results() -> list[PairwiseResult]:
    pending_rows: list[dict[str, Any]] = []

    for llm in DEFAULT_LLMS:
        if llm.label in EXCLUDED_LLMS:
            continue
        pooled_differences: list[int] = []
        pooled_paired_n = 0
        pooled_method_a_paired_correct = 0
        pooled_method_b_paired_correct = 0
        pooled_method_a_total = 0
        pooled_method_b_total = 0
        pooled_method_a_correct = 0
        pooled_method_b_correct = 0
        pooled_a_correct_b_wrong = 0
        pooled_a_wrong_b_correct = 0
        pooled_source_a: list[str] = []
        pooled_source_b: list[str] = []
        method_a_label = ""
        method_b_label = ""

        for dataset in DATASETS:
            methods: dict[str, MethodResults] = {}
            for framework_key, framework_label in FRAMEWORKS:
                method = _load_method_results(
                    llm.dir_for(framework_key),
                    framework_key,
                    framework_label,
                    dataset,
                )
                if method is not None:
                    methods[framework_key] = method

            method_a = methods.get(PLANNED_METHOD_PAIR[0])
            method_b = methods.get(PLANNED_METHOD_PAIR[1])
            if method_a is None or method_b is None:
                continue

            (
                paired_n,
                method_a_paired_correct,
                method_b_paired_correct,
                a_correct_b_wrong,
                a_wrong_b_correct,
                differences,
            ) = _paired_counts(method_a, method_b)
            mcnemar_p_value = _exact_mcnemar_p_value(
                a_correct_b_wrong,
                a_wrong_b_correct,
            )
            paired_t_statistic, paired_t_p_value = _paired_t_test(differences)
            pending_rows.append(
                {
                    "llm": llm.label,
                    "dataset": dataset.label,
                    "method_a_label": method_a.label,
                    "method_b_label": method_b.label,
                    "paired_n": paired_n,
                    "method_a_paired_correct": method_a_paired_correct,
                    "method_b_paired_correct": method_b_paired_correct,
                    "method_a_total": method_a.total,
                    "method_b_total": method_b.total,
                    "method_a_source_accuracy_pct": method_a.accuracy_pct,
                    "method_b_source_accuracy_pct": method_b.accuracy_pct,
                    "a_correct_b_wrong": a_correct_b_wrong,
                    "a_wrong_b_correct": a_wrong_b_correct,
                    "mcnemar_p_value": mcnemar_p_value,
                    "paired_t_statistic": paired_t_statistic,
                    "paired_t_p_value": paired_t_p_value,
                    "source_a": get_resolver().relative_to_repo(method_a.source_path),
                    "source_b": get_resolver().relative_to_repo(method_b.source_path),
                }
            )

            method_a_label = method_a.label
            method_b_label = method_b.label
            pooled_differences.extend(differences)
            pooled_paired_n += paired_n
            pooled_method_a_paired_correct += method_a_paired_correct
            pooled_method_b_paired_correct += method_b_paired_correct
            pooled_method_a_total += method_a.total
            pooled_method_b_total += method_b.total
            pooled_method_a_correct += method_a.correct
            pooled_method_b_correct += method_b.correct
            pooled_a_correct_b_wrong += a_correct_b_wrong
            pooled_a_wrong_b_correct += a_wrong_b_correct
            pooled_source_a.append(get_resolver().relative_to_repo(method_a.source_path))
            pooled_source_b.append(get_resolver().relative_to_repo(method_b.source_path))

        if pooled_paired_n:
            mcnemar_p_value = _exact_mcnemar_p_value(
                pooled_a_correct_b_wrong,
                pooled_a_wrong_b_correct,
            )
            paired_t_statistic, paired_t_p_value = _paired_t_test(
                pooled_differences
            )
            pending_rows.append(
                {
                    "llm": llm.label,
                    "dataset": "All datasets",
                    "method_a_label": method_a_label,
                    "method_b_label": method_b_label,
                    "paired_n": pooled_paired_n,
                    "method_a_paired_correct": pooled_method_a_paired_correct,
                    "method_b_paired_correct": pooled_method_b_paired_correct,
                    "method_a_total": pooled_method_a_total,
                    "method_b_total": pooled_method_b_total,
                    "method_a_source_accuracy_pct": (
                        pooled_method_a_correct / pooled_method_a_total * 100.0
                    ),
                    "method_b_source_accuracy_pct": (
                        pooled_method_b_correct / pooled_method_b_total * 100.0
                    ),
                    "a_correct_b_wrong": pooled_a_correct_b_wrong,
                    "a_wrong_b_correct": pooled_a_wrong_b_correct,
                    "mcnemar_p_value": mcnemar_p_value,
                    "paired_t_statistic": paired_t_statistic,
                    "paired_t_p_value": paired_t_p_value,
                    "source_a": ";".join(pooled_source_a),
                    "source_b": ";".join(pooled_source_b),
                }
            )

    results: list[PairwiseResult] = []
    for row in pending_rows:
        results.append(
            PairwiseResult(
                llm=row["llm"],
                dataset=row["dataset"],
                method_a=row["method_a_label"],
                method_b=row["method_b_label"],
                paired_n=row["paired_n"],
                method_a_paired_correct=row["method_a_paired_correct"],
                method_b_paired_correct=row["method_b_paired_correct"],
                method_a_total=row["method_a_total"],
                method_b_total=row["method_b_total"],
                method_a_source_accuracy_pct=row["method_a_source_accuracy_pct"],
                method_b_source_accuracy_pct=row["method_b_source_accuracy_pct"],
                paired_delta_accuracy_pct=(
                    (row["method_b_paired_correct"] - row["method_a_paired_correct"])
                    / row["paired_n"]
                    * 100.0
                ),
                a_correct_b_wrong=row["a_correct_b_wrong"],
                a_wrong_b_correct=row["a_wrong_b_correct"],
                mcnemar_p_value=row["mcnemar_p_value"],
                mcnemar_significant_0_05=row["mcnemar_p_value"] < ALPHA,
                paired_t_statistic=row["paired_t_statistic"],
                paired_t_p_value=row["paired_t_p_value"],
                paired_t_significant_0_05=row["paired_t_p_value"] < ALPHA,
                source_a=row["source_a"],
                source_b=row["source_b"],
            )
        )

    return results


def _fmt_float(value: float, digits: int = 6) -> str:
    return f"{value:.{digits}g}"


def _write_csv(path: Path, rows: list[PairwiseResult]) -> None:
    fieldnames = list(PairwiseResult.__dataclass_fields__)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    **row.__dict__,
                    "method_a_source_accuracy_pct": (
                        f"{row.method_a_source_accuracy_pct:.2f}"
                    ),
                    "method_b_source_accuracy_pct": (
                        f"{row.method_b_source_accuracy_pct:.2f}"
                    ),
                    "paired_delta_accuracy_pct": (
                        f"{row.paired_delta_accuracy_pct:.2f}"
                    ),
                    "mcnemar_p_value": _fmt_float(row.mcnemar_p_value),
                    "paired_t_statistic": _fmt_float(row.paired_t_statistic),
                    "paired_t_p_value": _fmt_float(row.paired_t_p_value),
                }
            )


def _write_markdown(path: Path, rows: list[PairwiseResult]) -> None:
    per_dataset_rows = [row for row in rows if row.dataset != "All datasets"]
    pooled_rows = [row for row in rows if row.dataset == "All datasets"]
    per_dataset_mcnemar_sig = [
        row for row in per_dataset_rows if row.mcnemar_significant_0_05
    ]
    per_dataset_ttest_sig = [
        row for row in per_dataset_rows if row.paired_t_significant_0_05
    ]
    pooled_mcnemar_sig = [row for row in pooled_rows if row.mcnemar_significant_0_05]
    pooled_ttest_sig = [row for row in pooled_rows if row.paired_t_significant_0_05]
    lines = [
        "# Table 2 Pairwise Significance",
        "",
        "Exact two-sided McNemar tests were computed from raw per-question "
        "correctness vectors for the planned comparison of Single-pass RAG "
        "with re-ranking against Agentic Workflow. Two-sided paired t-tests "
        "were also computed on the per-question correctness differences. "
        "When a raw file is partial, paired tests use the overlapping questions "
        "and report the paired sample size.",
        "",
        f"Per-dataset planned tests: {len(per_dataset_rows)}",
        (
            f"Per-dataset McNemar significant at alpha={ALPHA:g}: "
            f"{len(per_dataset_mcnemar_sig)}"
        ),
        (
            f"Per-dataset paired t-test significant at alpha={ALPHA:g}: "
            f"{len(per_dataset_ttest_sig)}"
        ),
        f"Pooled all-datasets tests: {len(pooled_rows)}",
        (
            f"Pooled McNemar significant at alpha={ALPHA:g}: "
            f"{len(pooled_mcnemar_sig)}"
        ),
        (
            f"Pooled paired t-test significant at alpha={ALPHA:g}: "
            f"{len(pooled_ttest_sig)}"
        ),
        "",
        "## Per-Dataset Comparisons",
        "",
        "| LLM | Dataset | Comparison | Paired n | Delta pp | McNemar p | McNemar sig. | Paired t | Paired t p | t-test sig. | Discordant (A>B / B>A) |",
        "| --- | --- | --- | ---: | ---: | ---: | --- | ---: | ---: | --- | ---: |",
    ]

    for row in per_dataset_rows:
        lines.append(
            "| "
            f"{row.llm} | {row.dataset} | "
            f"{row.method_a} -> {row.method_b} | "
            f"{row.paired_n} | "
            f"{row.paired_delta_accuracy_pct:.2f} | "
            f"{_fmt_float(row.mcnemar_p_value)} | "
            f"{'yes' if row.mcnemar_significant_0_05 else 'no'} | "
            f"{_fmt_float(row.paired_t_statistic)} | "
            f"{_fmt_float(row.paired_t_p_value)} | "
            f"{'yes' if row.paired_t_significant_0_05 else 'no'} | "
            f"{row.a_correct_b_wrong} / {row.a_wrong_b_correct} |"
        )
    lines.extend(
        [
            "",
            "## Pooled All-Datasets Comparisons",
            "",
            "| LLM | Paired n | Delta pp | McNemar p | McNemar sig. | Paired t | Paired t p | t-test sig. | Discordant (A>B / B>A) |",
            "| --- | ---: | ---: | ---: | --- | ---: | ---: | --- | ---: |",
        ]
    )
    for row in pooled_rows:
        lines.append(
            "| "
            f"{row.llm} | "
            f"{row.paired_n} | "
            f"{row.paired_delta_accuracy_pct:.2f} | "
            f"{_fmt_float(row.mcnemar_p_value)} | "
            f"{'yes' if row.mcnemar_significant_0_05 else 'no'} | "
            f"{_fmt_float(row.paired_t_statistic)} | "
            f"{_fmt_float(row.paired_t_p_value)} | "
            f"{'yes' if row.paired_t_significant_0_05 else 'no'} | "
            f"{row.a_correct_b_wrong} / {row.a_wrong_b_correct} |"
        )
    lines.append("")
    lines.append("The companion CSV includes source file paths for every test.")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _latex_escape(text: str) -> str:
    return (
        text.replace("\\", r"\textbackslash{}")
        .replace("&", r"\&")
        .replace("%", r"\%")
        .replace("$", r"\$")
        .replace("#", r"\#")
        .replace("_", r"\_")
        .replace("{", r"\{")
        .replace("}", r"\}")
        .replace("~", r"\textasciitilde{}")
        .replace("^", r"\textasciicircum{}")
    )


def _write_latex(path: Path, rows: list[PairwiseResult]) -> None:
    per_dataset_rows = [row for row in rows if row.dataset != "All datasets"]
    pooled_rows = [row for row in rows if row.dataset == "All datasets"]
    lines = [
        "% Auto-generated by evaluate/build_table2_significance.py",
        "% Preamble: \\usepackage{booktabs}",
        r"\begin{table}[ht]",
        r"\centering",
        (
            r"\caption{Pairwise statistical significance for Table 2 accuracy. "
            r"Exact two-sided McNemar tests compare Single-pass RAG with "
            r"re-ranking against Agentic Workflow using raw per-question "
            r"correctness; paired t-tests are also reported for comparison.}"
        ),
        r"\label{tab:table2-significance}",
        r"\begin{tabular}{l l r r r l r l}",
        r"\toprule",
        r"LLM & Dataset & Paired $n$ & $\Delta$ pp & McNemar $p$ & Sig. & t-test $p$ & Sig. \\",
        r"\midrule",
    ]

    for row in per_dataset_rows:
        lines.append(
            f"{_latex_escape(row.llm)} & "
            f"{_latex_escape(row.dataset)} & "
            f"{row.paired_n} & "
            f"{row.paired_delta_accuracy_pct:.2f} & "
            f"{_fmt_float(row.mcnemar_p_value)} & "
            f"{'yes' if row.mcnemar_significant_0_05 else 'no'} & "
            f"{_fmt_float(row.paired_t_p_value)} & "
            f"{'yes' if row.paired_t_significant_0_05 else 'no'} \\\\"
        )

    lines.extend(
        [
            r"\bottomrule",
            r"\end{tabular}",
            r"\par\smallskip",
            (
                r"\textit{Note:} Each row compares Single-pass RAG with "
                r"re-ranking to Agentic Workflow. Positive $\Delta$ means "
                r"Agentic Workflow is more accurate. "
                r"The paired t-test is applied to 0/1 per-question correctness "
                r"differences and is shown as a sensitivity analysis. "
                r"For partial raw files, paired $n$ is the number of overlapping "
                r"questions."
            ),
            r"\end{table}",
            "",
            r"\begin{table}[ht]",
            r"\centering",
            (
                r"\caption{Pooled pairwise statistical significance across all "
                r"Table 2 datasets. Exact two-sided McNemar tests compare "
                r"Single-pass RAG with re-ranking against Agentic Workflow after "
                r"combining raw per-question correctness across datasets; paired "
                r"t-tests are also reported for comparison.}"
            ),
            r"\label{tab:table2-significance-pooled}",
            r"\begin{tabular}{l r r r l r l}",
            r"\toprule",
            r"LLM & Paired $n$ & $\Delta$ pp & McNemar $p$ & Sig. & t-test $p$ & Sig. \\",
            r"\midrule",
        ]
    )
    for row in pooled_rows:
        lines.append(
            f"{_latex_escape(row.llm)} & "
            f"{row.paired_n} & "
            f"{row.paired_delta_accuracy_pct:.2f} & "
            f"{_fmt_float(row.mcnemar_p_value)} & "
            f"{'yes' if row.mcnemar_significant_0_05 else 'no'} & "
            f"{_fmt_float(row.paired_t_p_value)} & "
            f"{'yes' if row.paired_t_significant_0_05 else 'no'} \\\\"
        )
    lines.extend(
        [
            r"\bottomrule",
            r"\end{tabular}",
            r"\par\smallskip",
            (
                r"\textit{Note:} Each row pools all seven datasets for one LLM. "
                r"Positive $\Delta$ means Agentic Workflow is more accurate."
            ),
            r"\end{table}",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compute exact McNemar significance tests for Table 2.",
    )
    add_paper_analysis_args(parser)
    args = parser.parse_args(argv)
    repo_root, _manifest, output_dir = resolve_analysis_context(args)

    output_dir.mkdir(parents=True, exist_ok=True)
    rows = calculate_pairwise_results()

    csv_path = output_dir / f"{EXPORT_STEM}.csv"
    md_path = output_dir / f"{EXPORT_STEM}.md"
    tex_path = output_dir / f"{EXPORT_STEM}.tex"

    _write_csv(csv_path, rows)
    _write_markdown(md_path, rows)
    _write_latex(tex_path, rows)

    per_dataset_rows = [row for row in rows if row.dataset != "All datasets"]
    pooled_rows = [row for row in rows if row.dataset == "All datasets"]
    per_dataset_mcnemar_sig = sum(
        row.mcnemar_significant_0_05 for row in per_dataset_rows
    )
    per_dataset_ttest_sig = sum(
        row.paired_t_significant_0_05 for row in per_dataset_rows
    )
    pooled_mcnemar_sig = sum(row.mcnemar_significant_0_05 for row in pooled_rows)
    pooled_ttest_sig = sum(row.paired_t_significant_0_05 for row in pooled_rows)
    print(f"Wrote {csv_path.relative_to(repo_root)}")
    print(f"Wrote {md_path.relative_to(repo_root)}")
    print(f"Wrote {tex_path.relative_to(repo_root)}")
    print(
        f"Per-dataset McNemar significant comparisons (p < {ALPHA:g}): "
        f"{per_dataset_mcnemar_sig}/{len(per_dataset_rows)}"
    )
    print(
        f"Per-dataset paired t-test significant comparisons (p < {ALPHA:g}): "
        f"{per_dataset_ttest_sig}/{len(per_dataset_rows)}"
    )
    print(
        f"Pooled McNemar significant comparisons (p < {ALPHA:g}): "
        f"{pooled_mcnemar_sig}/{len(pooled_rows)}"
    )
    print(
        f"Pooled paired t-test significant comparisons (p < {ALPHA:g}): "
        f"{pooled_ttest_sig}/{len(pooled_rows)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
