# Reproduction Guide

This document describes how to recreate the EndoRAG paper experiments from the published repository. It covers environment setup, external artifacts, evaluation execution, analysis regeneration, hardware assumptions, and resume behavior.

## Prerequisites

- Linux workstation with NVIDIA GPU (paper runs used an **NVIDIA A6000** with CUDA)
- [Conda](https://docs.conda.io/) (Miniconda or Mambaforge)
- [Git LFS](https://git-lfs.com/) for pulling result JSON and log artifacts
- **Ollama** — local models and/or Ollama Cloud credentials for the four paper LLMs
- Hugging Face access for `Qwen/Qwen3-Reranker-8B` (loaded at evaluation time when reranking is enabled)

## Step 1 — Clone and install

```bash
git clone <repository-url> EndoRAG
cd EndoRAG
git lfs pull
conda env create -f environment.yml
conda activate endorag
```

The `endorag` environment installs Python 3.10, pinned pip dependencies from `requirements.txt`, and the package in editable mode (`pip install -e .`).

## Step 2 — Environment variables

Copy and edit `.env.example`:

```bash
cp .env.example .env
# export variables or use a dotenv loader before running commands
```

| Variable | Required for | Notes |
| --- | --- | --- |
| `ENDORAG_ARTIFACT_BASE_URL` | External download URIs | Base URL only; individual archives are listed in manifests |
| `ENDORAG_DOCUMENT_ROOT` | Indexing / path verification | Parent directory for extracted PDF corpora |
| `ENDORAG_CHROMA_ROOT` | Vector retrieval | Parent directory for extracted Chroma databases |
| `ENDORAG_OLLAMA_BASE_URL` | Local Ollama models | Default `http://localhost:11434` |
| `ENDORAG_OLLAMA_CLOUD_BASE_URL` | Cloud Ollama models | Default `https://ollama.com/v1` |
| `ENDORAG_OLLAMA_API_KEY` | Cloud models (`*:cloud`) | Required for gemma4, nemotron, minimax cloud endpoints |
| `OPENAI_API_KEY` | OpenAI embeddings only | Not needed for main paper configuration |
| `RERANK_DEVICE` | Cross-encoder reranking | Set to `cuda` on GPU hosts |
| `ENDORAG_RETRIEVAL_TOP_K` | Optional override | Default 5; manifests pin `top_k: 5` |
| `ENDORAG_MAX_RETRIEVAL_ROUNDS` | EndoRAG agent | Default 2 |

No credentials or private endpoints are hard-coded in source. The evaluation runner fails fast when a required model, index, or credential is missing.

## Step 3 — External artifacts

Source PDFs and prebuilt Chroma indexes are externalized. Manifests:

- `data/manifests/documents.yaml` — eight document archives (seven domain buckets + unified literature)
- `data/manifests/vector_databases.yaml` — nine vector-database archives (seven domain + Other + unified literature)

Archive URIs follow the pattern:

```text
${ENDORAG_ARTIFACT_BASE_URL}/documents/domain_specific/<name>.tar.gz
${ENDORAG_ARTIFACT_BASE_URL}/vector_databases/domain_specific/qwen3-embedding_8b/C512_O100/<collection>.tar.gz
```

SHA-256 digests in manifests are **pending external upload** until archives are published. After download, extract archives so paths match `expected_path` entries in the manifests (under `ENDORAG_DOCUMENT_ROOT` and `ENDORAG_CHROMA_ROOT`).

Verify manifest schema (no local paths required):

```bash
python scripts/setup/verify_external_artifacts.py
```

Verify local extraction:

```bash
python scripts/setup/verify_external_artifacts.py --require-paths
```

See [data_and_models.md](data_and_models.md) for corpus counts, collection names, and layout.

## Step 4 — Validate configuration (dry run)

Before any GPU run, validate manifests resolve correctly:

```bash
endorag evaluate --manifest configs/experiments/llm_only.yaml --dry-run
endorag evaluate --manifest configs/experiments/vector_rag_main.yaml --dry-run
endorag evaluate --manifest configs/experiments/endorag_main.yaml --dry-run
```

Each dry run prints the selected strategy, output path, and (for RAG methods) corpus entries without calling models.

## Step 5 — Regenerate paper analysis (fast path)

If you only need tables, figures, and significance tests from the **retained 360 result JSON files**, run:

```bash
endorag analyze --manifest configs/experiments/paper_analysis.yaml
```

The paper-analysis manifest intentionally resolves the retained historical artifacts,
even when newer complete evaluation outputs are present. This preserves the exact
provenance of the released paper results and requires no model reruns.

Outputs land in `results/analysis_exports/` (CSV, Markdown, LaTeX, PNG figures, diagnostic reports). This path does not require Ollama, Chroma, or reranker hardware.

Expected verification anchors (already regenerated in-repo):

| Metric | Value |
| --- | --- |
| Gemma EndoRAG macro accuracy | 88.71% |
| Gemma EndoRAG micro accuracy | 88.08% |
| Pooled McNemar *p* (reranked RAG → EndoRAG) | 0.0004095 |

## Step 6 — Full evaluation rerun (optional)

To rerun models from scratch you need local or cloud access to all four paper LLMs, extracted Chroma indexes, and the Qwen3-Reranker-8B cross-encoder on CUDA.

### LLM-only baseline

```bash
scripts/experiments/run_llm_only.sh
# or
endorag evaluate --manifest configs/experiments/llm_only.yaml
```

28 experiments (4 LLMs × 7 datasets). Typical per-question latency: 0.3–18 s depending on model (see Table 2 median times in analysis exports).

### Vector RAG (main paper configuration)

```bash
scripts/experiments/run_vector_rag.sh
# or
endorag evaluate --manifest configs/experiments/vector_rag_main.yaml
```

Uses `qwen3-embedding:8b`, chunk 512 / overlap 100, `Qwen/Qwen3-Reranker-8B`, domain-specific corpus (`configs/corpora/domain_specific.yaml`).

### EndoRAG agentic workflow

```bash
scripts/experiments/run_endorag.sh
# or
endorag evaluate --manifest configs/experiments/endorag_main.yaml
```

EndoRAG runs are substantially slower (median ~40–166 s per question depending on LLM; see Table 2).

### Ablations

| Experiment family | Manifest | Wrapper |
| --- | --- | --- |
| Embedding comparison (Figure 2) | `configs/experiments/embedding_ablation.yaml` | `scripts/experiments/run_embedding_ablation.sh` |
| Reranker sensitivity (Table 3) | `configs/experiments/reranker_ablation.yaml` | `scripts/experiments/run_reranker_ablation.sh` |
| Oracle routing | `configs/experiments/oracle_routing.yaml` | `scripts/experiments/run_oracle_routing.sh` |
| Unified literature corpus | `configs/experiments/literature_corpus.yaml` | `scripts/experiments/run_literature_corpus.sh` |

### Build indexes from source documents (optional)

If you have extracted PDFs but not prebuilt Chroma archives:

```bash
endorag index build --manifest configs/corpora/domain_specific.yaml
```

Indexing uses Docling with OCR (`configs/docling.yaml`) and paper chunking 512/100.

## Generation and retrieval defaults

Recovered from manifests and `src/endorag/providers/models.py`:

| Setting | Value |
| --- | --- |
| Random seed | 42 |
| LLM temperature | 0 |
| LLM context window | 128000 tokens |
| LLM top-p | 0.9 |
| LLM top-k | 40 |
| LLM max tokens | 4096 |
| Retrieval top-k | 5 |
| Rerank candidate multiplier | 4.0 (20 candidates before rerank) |
| Rerank top-n | 5 |
| Chunk size / overlap | 512 / 100 |

## Resume and overwrite behavior

Evaluation writes deterministic per-question JSON under each manifest `output` path.

- **`--resume`** — continue from compatible partial output, skipping completed questions;
  in multi-experiment manifests it also skips experiments whose outputs are already complete.
- **`--overwrite`** — replace a complete result file (requires explicit flag).
- Without either flag, the runner refuses to clobber existing complete output.

Before resuming, the runner verifies that the saved resolved manifest, question text,
and expected answers match the requested experiment. Use `--overwrite` when changing
models, retrieval settings, routing mode, or dataset content. Partial results are
preserved after recoverable provider failures. Logs are sanitized before publication
(no credentials or private headers).

## Runtime metadata

Each evaluation saves resolved configuration beside outputs. Record hardware and package versions for new runs:

```bash
endorag environment report --output results/environment_report.json
```

## Ollama setup notes

Paper LLMs:

| Model | Endpoint mode |
| --- | --- |
| `gemma4:31b-cloud` | Ollama Cloud |
| `nemotron-3-nano:30b-cloud` | Ollama Cloud |
| `mistral-small3.2:24b` | Local Ollama |
| `minimax-m2.7:cloud` | Ollama Cloud |

Cloud models require `ENDORAG_OLLAMA_API_KEY`. Local Mistral requires the model pulled into your Ollama instance. Embedding model for main results: `qwen3-embedding:8b`.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| Missing Chroma collection | Run `verify_external_artifacts.py --require-paths`; confirm `ENDORAG_CHROMA_ROOT` |
| Reranker CUDA OOM | Ensure `RERANK_DEVICE=cuda` on a GPU with sufficient memory; A6000 was used in paper runs |
| Cloud model auth failure | Set `ENDORAG_OLLAMA_API_KEY`; confirm model name matches manifest |
| Analysis script missing JSON | See [result_inventory.md](result_inventory.md); EndoRAG JSONs live under `results/Method_vectorRAG/` |
| Incomplete EndoRAG logs | Only 7 of 28 EndoRAG sanitized logs were retained from historical runs; result JSONs are complete |

## Related documentation

- [paper_to_code.md](paper_to_code.md) — manuscript artifact mapping
- [data_and_models.md](data_and_models.md) — datasets, corpora, models
- [result_inventory.md](result_inventory.md) — retained artifacts
