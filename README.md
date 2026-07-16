# EndoRAG

EndoRAG is an agentic retrieval-augmented generation framework for endocrinology multiple-choice question answering, developed at the AIHN Lab, ARTORG Center for Biomedical Engineering Research, University of Bern. This repository publishes the original implementation of EndoRAG.

## Architecture

EndoRAG supports three evaluation methods:

1. **LLM-only** — direct multiple-choice answering from model weights, without retrieval.
2. **Single-pass vector RAG** — domain-routed Chroma retrieval, optional cross-encoder reranking, and one-shot MCQ answering.
3. **EndoRAG (agentic workflow)** — a multi-stage agent that plans retrieval, checks answerability, performs follow-up retrieval, reasons over options, verifies evidence, and arbitrates between agentic and baseline answers.

Core package layout:

```text
src/endorag/
├── agent/          # EndoRAG orchestration, skills, planning
├── providers/      # LLM and embedding bootstrap (Ollama local/cloud, OpenAI)
├── ingestion/      # Docling document parsing and chunking
├── retrieval/      # Chroma collections, routing, reranking
├── evaluation/     # Manifest-driven runners for all three methods
├── analysis/       # Paper table/figure regeneration from saved results
└── cli.py          # endorag command-line interface
```

External source documents and prebuilt Chroma indexes are **not** committed to Git. They are declared in `data/manifests/` and fetched or verified through `${ENDORAG_ARTIFACT_BASE_URL}`.
This are the links for accessing them:
1. Sounce documents -> X
2. Chroma indexes -> X 

## Quick start

### 1. Create the Conda environment

```bash
conda env create -f environment.yml
conda activate endorag
```

This creates the `endorag` environment (Python 3.10) and installs the package in editable mode.

### 2. Configure environment variables

Copy `.env.example` and set paths and credentials:

```bash
cp .env.example .env
```


| Variable                        | Purpose                                                                |
| ------------------------------- | ---------------------------------------------------------------------- |
| `ENDORAG_ARTIFACT_BASE_URL`     | Base URL for external document and vector-database archives            |
| `ENDORAG_DOCUMENT_ROOT`         | Local directory for extracted source PDFs                              |
| `ENDORAG_CHROMA_ROOT`           | Local directory for extracted Chroma databases                         |
| `ENDORAG_OLLAMA_BASE_URL`       | Local Ollama endpoint (default `http://localhost:11434`)               |
| `ENDORAG_OLLAMA_CLOUD_BASE_URL` | Ollama cloud API base                                                  |
| `ENDORAG_OLLAMA_API_KEY`        | API key for cloud-hosted Ollama models                                 |
| `OPENAI_API_KEY`                | Required only for OpenAI embedding models                              |
| `RERANK_DEVICE`                 | Cross-encoder device (default `cuda`; paper runs used an NVIDIA A6000) |


### 3. Verify manifests

```bash
python scripts/setup/verify_external_artifacts.py
```

Use `--require-paths` after downloading and extracting external archives. See [docs/reproducibility.md](docs/reproducibility.md) and [docs/data_and_models.md](docs/data_and_models.md) for the full artifact workflow.

### 4. Regenerate paper analysis from retained results

The repository ships 360 retained paper-run result JSON files and 316 sanitized logs
(Git LFS). These artifacts use the historical directory layout described below. To
regenerate publication tables and figures without rerunning models:

```bash
endorag analyze --manifest configs/experiments/paper_analysis.yaml
```

Verified headline numbers (Gemma EndoRAG, Cosine C512/100, Qwen3-Reranker-8B): **88.71% macro**, **88.08% micro** accuracy; pooled McNemar *p* = 0.0004095 (EndoRAG vs reranked single-pass RAG).

## Evaluation methods


| Method                         | CLI manifest                               | Shell wrapper                           |
| ------------------------------ | ------------------------------------------ | --------------------------------------- |
| LLM-only                       | `configs/experiments/llm_only.yaml`        | `scripts/experiments/run_llm_only.sh`   |
| Vector RAG (main paper config) | `configs/experiments/vector_rag_main.yaml` | `scripts/experiments/run_vector_rag.sh` |
| EndoRAG                        | `configs/experiments/endorag_main.yaml`    | `scripts/experiments/run_endorag.sh`    |


Additional ablation manifests: `embedding_ablation.yaml`, `reranker_ablation.yaml`, `oracle_routing.yaml`, `literature_corpus.yaml`.

Dry-run any manifest before launching GPU jobs:

```bash
endorag evaluate --manifest configs/experiments/llm_only.yaml --dry-run
```

## Datasets

Seven authorized MCQ datasets (386 questions total) live in `data/datasets/` with stable IDs and routing metadata in `data/routing/`. See [docs/data_and_models.md](docs/data_and_models.md) for per-dataset counts and provenance.

## Results layout

New evaluations write to the exact `output` and `log` paths declared in the selected
experiment manifest. For the three main method manifests, the current result layout is:

```text
results/
├── Method_LLM/
│   └── <llm>/<dataset>_LLM_<llm>_1.json
├── Method_vectorRAG/
│   └── <llm>/<embedding>/Cosine_C512_100/rerank_qwen8b/
│       └── rerank_qwen8b_<reranker>_<dataset>_<configuration>_1.json
└── Method_endorag/
    └── <llm>/<embedding>/Cosine_C512_100/rerank_qwen8b/
        └── endorag_<reranker>_<dataset>_<configuration>_1.json
```

Each evaluation also creates an adjacent provenance sidecar with the same basename and
the suffix `.environment.json`. Console logs are written under `logs/` at the path
declared by the same manifest. Run with `--dry-run` to print the concrete paths before
any model call.

The retained paper artifacts predate this cleaned layout and intentionally remain at
their historical locations:

```text
results/
├── Method_LLM/<llm>/LLM/...
└── Method_vectorRAG/<llm>/<embedding>/
    ├── LLM/Cosine_C512_100/...       # retained single-pass RAG
    └── agentic_workflow_8B/...       # retained EndoRAG
```

Oracle-routing and literature-corpus artifacts remain below
`results/Method_vectorRAG/oracle/` and `results/Method_vectorRAG/literature/`.
Regenerated tables, figures, and reports are written to
`results/analysis_exports/`.

Paper analysis is deliberately pinned to the retained historical artifacts; a newly
generated file under the current layout does not replace an input to the published
tables. This keeps the released results reproducible without rerunning the models.

Full retained-artifact inventory: [docs/result_inventory.md](docs/result_inventory.md).

## Paper reproduction

Complete mapping from manuscript tables and figures to manifests, commands, and output paths: [docs/paper_to_code.md](docs/paper_to_code.md).

Step-by-step reproduction (environment, hardware, resume behavior, runtime): [docs/reproducibility.md](docs/reproducibility.md).

## CLI reference

```text
endorag index build --manifest <corpus-manifest> [--dry-run]
endorag evaluate --manifest <experiment-manifest> [--resume] [--overwrite] [--dry-run]
endorag analyze --manifest configs/experiments/paper_analysis.yaml
endorag environment report --output <path.json>
```

## License and citation

Licensed under the [Apache License 2.0](LICENSE). See [NOTICE](NOTICE) for copyright attribution.

If you use EndoRAG, please cite the associated paper:

```bibtex
@misc{endorag2026,
  title  = {EndoRAG: An Agentic Retrieval-Augmented Generation Framework for Endocrinology Question Answering},
  author = {Panagiotou, Maria, Abdur Rahman, Lubnaa, Papathanail, Ioannis and Mougiakakou, Stavroula},
  year   = {2026}
}
```
