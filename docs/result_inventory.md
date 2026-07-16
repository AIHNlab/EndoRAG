# Result and Log Inventory

This document inventories the paper-related artifacts retained in the EndoRAG repository. Selection criteria and provenance are recorded in `data/manifests/paper_results.json` and `data/manifests/paper_logs.json`.

## Totals

| Artifact type | Count | Storage |
| --- | ---: | --- |
| Evaluation result JSON | 360 | Git LFS (`results/Method_LLM/`, `results/Method_vectorRAG/`) |
| Sanitized run logs | 316 | Git LFS (`logs/`) |
| Analysis exports | 21 files + 2 figures | Normal Git (`results/analysis_exports/`) |
| MCQ dataset records | 386 questions | Normal Git (`data/datasets/`) |

## Result JSON by experiment family

| Family | Expected | Retained | Manifest |
| --- | ---: | ---: | --- |
| LLM-only | 28 | 28 | `configs/experiments/llm_only.yaml` |
| Embedding ablation (no rerank) | 140 | 140 | `configs/experiments/embedding_ablation.yaml` |
| Reranker ablation | 112 | 112 | `configs/experiments/reranker_ablation.yaml` |
| EndoRAG (agentic workflow) | 28 | 28 | `configs/experiments/endorag_main.yaml` |
| Oracle routing | 24 | 24 | `configs/experiments/oracle_routing.yaml` |
| Unified literature corpus | 28 | 28 | `configs/experiments/literature_corpus.yaml` |
| **Total** | **360** | **360** | `data/manifests/paper_results.json` |

Paper LLMs only: `gemma4:31b-cloud`, `nemotron-3-nano:30b-cloud`, `mistral-small3.2:24b`, `minimax-m2.7:cloud`.

## Result directory layout

```text
results/
├── Method_LLM/
│   └── <llm>/
│       └── <Dataset>_LLM_<llm>_1.json
└── Method_vectorRAG/
    ├── <llm>/
    │   ├── <embedding>/Cosine_C512_100/          # embedding ablation
    │   └── qwen3-embedding:8b/Cosine_C512_100/
    │       ├── rerank_qwen8b/                      # main RAG + EndoRAG
    │       ├── rerank_<other>/                     # reranker ablation
    │       └── LLM/Cosine_C512_100/                # no-rerank qwen baseline rows
    ├── oracle/<llm>/qwen3-embedding:8b/...
    └── literature/<llm>/qwen3-embedding:8b/...
```

EndoRAG JSON filenames contain the `endorag_` prefix or legacy `agentic_workflow` stem. The analysis resolver searches `agentic_workflow_8B`, `agentic_workflow`, `endorag_qwen8b`, and `endorag` subdirectories.

## Log inventory by family

From `data/manifests/paper_logs.json`:

| Family | Logs retained |
| --- | ---: |
| LLM-only | 9 |
| Embedding ablation | 136 |
| Reranker ablation | 112 |
| EndoRAG | 7 |
| Oracle routing | 24 |
| Literature corpus | 28 |
| **Total** | **316** |

### Known log gaps

- **LLM-only:** Nemotron logs missing for all 7 datasets (28 result JSONs exist; 9 of 28 logs retained — Gemma, Mistral, Minimax subsets).
- **EndoRAG:** Only 7 historical agentic-workflow logs survived sanitization (`logs/agentic_workflow/`) despite 28 EndoRAG result JSONs. Result payloads are complete; logs are supplementary.

Missing log entries are enumerated in the `missing` array of `data/manifests/paper_logs.json`.

## Analysis exports (regenerated)

Produced by `endorag analyze --manifest configs/experiments/paper_analysis.yaml`:

| Export | Description |
| --- | --- |
| `table2_accuracy_cosine_c512_100.{csv,md,tex}` | Main accuracy table |
| `table2_significance_cosine_c512_100.{csv,md,tex}` | McNemar and paired *t*-tests |
| `table3_reranker_sensitivity_cosine_c512_100.{csv,md,tex}` | Reranker ablation |
| `table_embedding_comparison_cosine_c512_100.{csv,md,tex}` | Embedding comparison |
| `table_literature_vs_qwen_rerank.{csv,md,tex}` | Unified vs domain corpus |
| `table_rerank_oracle_vs_no_oracle_accuracy.{csv,md}` | Oracle routing |
| `figures/vector_rag_radar_combined*.png` | Embedding radar figures |
| `agentic_vs_vector_rag_report.md` | Qualitative divergence study |
| `best_endorag_failure_modes_report.md` | Failure and contamination analysis |

## Excluded artifacts

The following were present in the source KnowledgeBase repository but **not** copied:

- `phi4:latest`, `qwen3:30b`, local bare `gemma4:31b` runs
- Qwen3-Reranker-4B and other non-paper rerankers
- Incomplete chunk-size / parser exploration grids (C400, C1024, BM25, MMR)
- Duplicate Mistral EndoRAG AdrenalGlands JSON (one canonical file retained)
- Unsanitized logs containing credentials or private infrastructure details

## Verification commands

```bash
# Result count
find results -name '*.json' | wc -l   # expect 360

# Log count
find logs -name '*.log' | wc -l       # expect 316

# Regenerate analysis
endorag analyze --manifest configs/experiments/paper_analysis.yaml
```

## Git LFS

Result JSON and log files carry Git LFS filter attributes. Run `git lfs pull` after clone to materialize large artifacts.
