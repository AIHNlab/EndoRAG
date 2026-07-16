# Paper-to-Code Mapping

This document maps each manuscript table, figure, and analysis to the experiment manifest, CLI command, raw result paths, analysis script, and generated export in this repository.

Analysis hub manifest: `configs/experiments/paper_analysis.yaml`

Regenerate everything:

```bash
endorag analyze --manifest configs/experiments/paper_analysis.yaml
```

Individual scripts accept `--manifest`, `--repo-root`, and `--output-dir` (see `src/endorag/analysis/driver.py`).

---

## Table 1 — Corpus statistics

**Manuscript content:** Document counts per endocrine domain and exclusion statistics during ingestion.

**Code / data sources:**

| Item | Location |
| --- | --- |
| Per-domain document counts | `data/manifests/documents.yaml` (`document_count` fields) |
| Vector collection metadata | `data/manifests/vector_databases.yaml` |
| Domain routing buckets | `configs/corpora/domain_specific.yaml` |
| Docling pipeline settings | `configs/docling.yaml` |
| Index build command | `endorag index build --manifest configs/corpora/domain_specific.yaml` |

**Regenerated export:** Table 1 is derived from manifest metadata rather than a dedicated analysis script. Counts are documented in [data_and_models.md](data_and_models.md).

**Note:** The manuscript describes six clinical domains; the implementation adds an **`Other`** bucket for three endocrinology textbooks. See [implementation_differences.md](implementation_differences.md) for exclusion-count discrepancies.

---

## Figure 1A–C — EndoRAG architecture and workflow

**Manuscript content:** Agentic pipeline stages (planning, retrieval, answerability, reasoning, verification, arbitration).

**Code sources:**

| Panel | Location |
| --- | --- |
| Agent orchestration | `src/endorag/agent/` |
| Planning manager | `src/endorag/agent/agents/planning_manager.py` |
| Typed skills | `src/endorag/agent/skills/` (retrieve, answerability, reasoning, verification) |
| Evaluation wrapper | `src/endorag/evaluation/strategies/endorag.py` |
| Stage diagnostics in results | `flow_diagnostics` fields in EndoRAG result JSON |

**Run command:**

```bash
endorag evaluate --manifest configs/experiments/endorag_main.yaml
# or scripts/experiments/run_endorag.sh
```

**Raw results:** `results/Method_vectorRAG/<llm>/qwen3-embedding:8b/Cosine_C512_100/rerank_qwen8b/endorag_*.json`

(EndoRAG JSONs were copied from historical `agentic_workflow_*` outputs during artifact extraction; resolver aliases in `src/endorag/analysis/artifact_resolver.py`.)

**Regenerated export:** Architecture is code-level; no standalone figure script. Workflow behavior is analyzed in `results/analysis_exports/agentic_vs_vector_rag_report.md`.

---

## Table 2 — Main accuracy comparison

**Manuscript content:** LLM-only, single-pass RAG (with/without rerank), and EndoRAG accuracy across seven datasets and four LLMs; routing accuracy for EndoRAG; median latency.

**Manifests:**

| Framework row | Result source | Evaluate manifest |
| --- | --- | --- |
| LLM | `results/Method_LLM/` | `configs/experiments/llm_only.yaml` |
| Single-pass RAG w/o rerank | `results/Method_vectorRAG/<llm>/qwen3-embedding:8b/Cosine_C512_100/` (no rerank prefix) | `configs/experiments/embedding_ablation.yaml` (qwen3 rows) |
| Single-pass RAG w/ rerank | `results/Method_vectorRAG/<llm>/qwen3-embedding:8b/Cosine_C512_100/rerank_qwen8b/` | `configs/experiments/vector_rag_main.yaml` |
| Agentic Workflow (EndoRAG) | `results/Method_vectorRAG/<llm>/qwen3-embedding:8b/Cosine_C512_100/rerank_qwen8b/endorag_*.json` | `configs/experiments/endorag_main.yaml` |

**Analysis script:** `scripts/analysis/build_table2_accuracy.py`

**Exports:**

- `results/analysis_exports/table2_accuracy_cosine_c512_100.csv`
- `results/analysis_exports/table2_accuracy_cosine_c512_100.md`
- `results/analysis_exports/table2_accuracy_cosine_c512_100.tex`

**Verified anchors:** Gemma EndoRAG macro 88.71%, micro 88.08%.

---

## Table 2 — Significance (McNemar and paired *t*-tests)

**Manuscript content:** Pairwise significance of reranked single-pass RAG vs EndoRAG.

**Analysis script:** `scripts/analysis/build_table2_significance.py`

**Exports:**

- `results/analysis_exports/table2_significance_cosine_c512_100.csv`
- `results/analysis_exports/table2_significance_cosine_c512_100.md`
- `results/analysis_exports/table2_significance_cosine_c512_100.tex`

**Verified anchor:** Pooled Gemma McNemar *p* = 0.000409541 (386 paired questions).

---

## Figure 2 — Embedding model comparison (radar)

**Manuscript content:** Five embedding models across LLM backbones (no rerank).

**Evaluate manifest:** `configs/experiments/embedding_ablation.yaml` (140 experiments)

**Raw results:** `results/Method_vectorRAG/<llm>/<embedding>/Cosine_C512_100/*.json`

**Analysis scripts:**

- `scripts/analysis/build_table_embedding_comparison.py` → table
- `scripts/analysis/plot_embedding_radar.py` → radar figures

**Exports:**

- `results/analysis_exports/table_embedding_comparison_cosine_c512_100.{csv,md,tex}`
- `results/analysis_exports/figures/vector_rag_radar_combined.png`
- `results/analysis_exports/figures/vector_rag_radar_combined_2x4.png`

---

## Table 3 — Reranker sensitivity

**Manuscript content:** Four rerankers plus no-rerank baseline with qwen3-embedding:8b.

**Evaluate manifest:** `configs/experiments/reranker_ablation.yaml` (112 experiments)

**Raw results:** `results/Method_vectorRAG/<llm>/qwen3-embedding:8b/Cosine_C512_100/rerank_*/`

**Analysis script:** `scripts/analysis/build_table3_reranker_sensitivity.py`

**Exports:**

- `results/analysis_exports/table3_reranker_sensitivity_cosine_c512_100.{csv,md,tex}`

---

## Oracle routing ablation

**Manuscript content:** Effect of perfect domain routing on reranked vector RAG (UKEU excluded from oracle table).

**Evaluate manifest:** `configs/experiments/oracle_routing.yaml` (24 experiments)

**Raw results:** `results/Method_vectorRAG/oracle/<llm>/qwen3-embedding:8b/Cosine_C512_100/rerank_qwen8b/`

**Analysis script:** `scripts/analysis/build_table_oracle_routing.py`

**Exports:**

- `results/analysis_exports/table_rerank_oracle_vs_no_oracle_accuracy.{csv,md}`

---

## Unified vs domain-specific corpus

**Manuscript content:** Literature-unified index vs domain-specific collections (Qwen3-Reranker-8B).

**Evaluate manifest:** `configs/experiments/literature_corpus.yaml` (28 experiments)

**Raw results:**

- Domain-specific: `results/Method_vectorRAG/<llm>/qwen3-embedding:8b/...` (main manifest)
- Unified: `results/Method_vectorRAG/literature/<llm>/qwen3-embedding:8b/...`

**Analysis script:** `scripts/analysis/build_table_literature_vs_qwen_rerank.py`

**Exports:**

- `results/analysis_exports/table_literature_vs_qwen_rerank.{csv,md,tex}`

---

## Agentic vs vector RAG divergence analysis

**Manuscript content:** Qualitative comparison of EndoRAG and reranked RAG decisions.

**Analysis script:** `scripts/analysis/compare_endorag_vs_vector_rag.py`

**Export:** `results/analysis_exports/agentic_vs_vector_rag_report.md`

Default deep-dive uses nemotron-3-nano result paths (inherited from source analysis).

---

## Failure modes and contamination analysis

**Manuscript content:** EndoRAG failure taxonomy; overlap with LLM-only and RAG errors; contamination checks.

**Analysis module:** `src/endorag/analysis/investigate_failures.py`

**Export:** `results/analysis_exports/best_endorag_failure_modes_report.md`

Invoked automatically by `endorag analyze`.

---

## Summary matrix

| Manuscript artifact | Analysis script | Primary export |
| --- | --- | --- |
| Table 1 | (manifest metadata) | [data_and_models.md](data_and_models.md) |
| Figure 1A–C | — | `src/endorag/agent/` |
| Table 2 accuracy | `build_table2_accuracy.py` | `table2_accuracy_cosine_c512_100.*` |
| Table 2 significance | `build_table2_significance.py` | `table2_significance_cosine_c512_100.*` |
| Figure 2 | `plot_embedding_radar.py` | `figures/vector_rag_radar_combined*.png` |
| Embedding table | `build_table_embedding_comparison.py` | `table_embedding_comparison_cosine_c512_100.*` |
| Table 3 | `build_table3_reranker_sensitivity.py` | `table3_reranker_sensitivity_cosine_c512_100.*` |
| Oracle routing | `build_table_oracle_routing.py` | `table_rerank_oracle_vs_no_oracle_accuracy.*` |
| Unified corpus | `build_table_literature_vs_qwen_rerank.py` | `table_literature_vs_qwen_rerank.*` |
| Agentic divergence | `compare_endorag_vs_vector_rag.py` | `agentic_vs_vector_rag_report.md` |
| Failure / contamination | `investigate_failures.py` | `best_endorag_failure_modes_report.md` |
