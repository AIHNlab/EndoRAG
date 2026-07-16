"""Run the full paper analysis export suite."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ANALYSIS_SCRIPTS: tuple[tuple[str, str], ...] = (
    ("build_table2_accuracy.py", "results/analysis_exports"),
    ("build_table2_significance.py", "results/analysis_exports"),
    ("build_table3_reranker_sensitivity.py", "results/analysis_exports"),
    ("build_table_embedding_comparison.py", "results/analysis_exports"),
    ("build_table_literature_vs_qwen_rerank.py", "results/analysis_exports"),
    ("build_table_oracle_routing.py", "results/analysis_exports"),
    ("plot_embedding_radar.py", "results/analysis_exports/figures"),
    ("compare_endorag_vs_vector_rag.py", "results/analysis_exports"),
)


def run_analysis_suite(
    manifest: Path,
    repo_root: Path,
    output_dir: Path | None = None,
) -> int:
    scripts_dir = repo_root / "scripts" / "analysis"
    base_output = output_dir or (repo_root / "results" / "analysis_exports")
    failures: list[str] = []

    for script_name, rel_output in ANALYSIS_SCRIPTS:
        script_path = scripts_dir / script_name
        if not script_path.is_file():
            failures.append(f"missing script: {script_path}")
            continue
        out = base_output if rel_output == "results/analysis_exports" else repo_root / rel_output
        cmd = [
            sys.executable,
            str(script_path),
            "--manifest",
            str(manifest),
            "--repo-root",
            str(repo_root),
            "--output-dir",
            str(out),
        ]
        print(f"Running {script_name}...", flush=True)
        result = subprocess.run(cmd, cwd=repo_root, check=False)
        if result.returncode != 0:
            failures.append(f"{script_name} exited {result.returncode}")

    module_cmd = [
        sys.executable,
        "-m",
        "endorag.analysis.investigate_failures",
        "--manifest",
        str(manifest),
        "--repo-root",
        str(repo_root),
        "--output-dir",
        str(base_output),
    ]
    print("Running investigate_failures...", flush=True)
    result = subprocess.run(module_cmd, cwd=repo_root, check=False)
    if result.returncode != 0:
        failures.append(f"investigate_failures exited {result.returncode}")

    if failures:
        print("Analysis suite failures:", file=sys.stderr)
        for item in failures:
            print(f"  - {item}", file=sys.stderr)
        return 1
    return 0
