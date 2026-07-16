# Data and Models

This document describes the MCQ datasets, external document corpora, vector indexes, and model configurations used in EndoRAG paper experiments.

## MCQ datasets

Seven authorized datasets are committed under `data/datasets/` (386 questions total):

| File | Questions | Role |
| --- | ---: | --- |
| `MCQs_sample_questions2015_full.json` | 53 | Diabetes monotopic (textbook MCQs) |
| `AdrenalGlands_dataset.json` | 50 | Adrenal monotopic |
| `ThyroidGland_dataset.json` | 59 | Thyroid monotopic |
| `ParathyroidGlandAndBoneDisease_dataset.json` | 43 | Parathyroid monotopic |
| `PituitaryGlandAndHypothalamus_dataset.json` | 54 | Pituitary monotopic |
| `ReproductiveEndocrinology_dataset.json` | 42 | Reproductive monotopic |
| `UKEU.json` | 85 | Multi-domain (UK/European exam mix) |

Each record includes stable question identifiers, answer options, ground-truth letter, category metadata, and provenance fields suitable for reproducibility.

### Routing maps

Domain routing for mixed datasets uses pinned maps in `data/routing/`:

| File | Used for |
| --- | --- |
| `mcq_diabetes_category_map.json` | Diabetes MCQ book question → domain |
| `ukeu_category_map.json` | UKEU question → domain |

These maps drive EndoRAG routing evaluation and oracle-routing ablations.

## External document corpora

Source PDFs are **not** in Git. `data/manifests/documents.yaml` declares eight downloadable archives resolved from `${ENDORAG_ARTIFACT_BASE_URL}`:

| Logical name | Category | Documents | Local relative path |
| --- | --- | ---: | --- |
| `diabetes_literature` | Diabetes and Lipid Metabolism | 111 | `DiabetesLiterature/` |
| `pituitary_literature` | Pituitary Gland and Hypothalamus | 116 | `PituitaryLiterature/` |
| `thyroid_literature` | Thyroid Gland | 60 | `ThyroidLiterature/` |
| `parathyroid_literature` | Parathyroid Gland and Bone Disease | 79 | `ParathyroidLiterature/` |
| `reproductive_endocrinology_literature` | Reproductive Endocrinology, Andrology and Sexual Function | 85 | `ReproductiveEndocrinologyLiterature/` |
| `adrenal_literature` | Adrenal Glands | 42 | `AdrenalLiterature/` |
| `endocrinology_books` | Other (default bucket) | 3 | `EndocrinologyBooks/` |
| `literature_documents` | Unified literature corpus | 482 | `LiteratureDocuments/` |

**Domain-specific total:** 496 documents across seven buckets (including three endocrinology textbooks in `Other`).

**Unified literature total:** 482 documents (single combined archive for literature-corpus ablation).

SHA-256 checksums in manifests are pending external upload. After publication, verify with:

```bash
python scripts/setup/verify_external_artifacts.py --require-paths
```

### Corpus configuration files

| File | Purpose |
| --- | --- |
| `configs/corpora/domain_specific.yaml` | Seven-bucket domain routing (includes `Other` default on textbooks) |
| `configs/corpora/literature_unified.yaml` | Single unified literature collection |

## Vector databases

Prebuilt Chroma indexes are externalized in `data/manifests/vector_databases.yaml`. Paper defaults:

- Embedding model: `qwen3-embedding:8b`
- Chunk size / overlap: 512 / 100
- Path segment: `C512_O100`

Nine archives (domain buckets + Other + unified literature) extract under `${ENDORAG_CHROMA_ROOT}/qwen3-embedding:8b/chunk_512/<collection>/`.

Embedding-ablation experiments use alternate embedding models (see below) and expect sibling directories under each LLM in `results/Method_vectorRAG/`.

## Paper LLMs

Four LLMs appear in all main tables (exploratory models such as `qwen3:30b` and `phi4:latest` are excluded from this repository):

| Model | Provider | Endpoint |
| --- | --- | --- |
| `gemma4:31b-cloud` | Ollama Cloud | Cloud API |
| `nemotron-3-nano:30b-cloud` | Ollama Cloud | Cloud API |
| `mistral-small3.2:24b` | Ollama | Local |
| `minimax-m2.7:cloud` | Ollama Cloud | Cloud API |

Generation defaults (`src/endorag/providers/models.py`): temperature 0, seed 42, top-p 0.9, top-k 40, num_ctx 128000, num_predict 4096.

## Embedding models (Figure 2 ablation)

Five embedding models × four LLMs × seven datasets = 140 no-rerank vector RAG runs:

| Embedding model | Manifest directory slug |
| --- | --- |
| `embeddinggemma` | `embeddinggemma/` |
| `bge-m3:latest` | `bge-m3:latest/` |
| `text-embedding-3-large` | `text-embedding-3-large/` |
| `nomic-embed-text` | `nomic-embed-text/` |
| `qwen3-embedding:8b` | `qwen3-embedding:8b/` |

Main Table 2 EndoRAG and reranked RAG rows use **qwen3-embedding:8b** only.

## Rerankers (Table 3 ablation)

Main paper configuration and EndoRAG runs use:

- Model: `Qwen/Qwen3-Reranker-8B`
- Instruction: clinical endocrinology MCQ relevance (see `configs/rerank.yaml`)
- Device: CUDA (`RERANK_DEVICE=cuda`)
- top-k after rerank: 5
- candidate pool: 20 (multiplier 4 × top-k 5)

Table 3 additionally compares `bge-reranker-v2-m3`, `jina-reranker-v3`, `ms-marco-MiniLM-L-6-v2`, and a no-rerank baseline.

## Docling ingestion

Document parsing configuration: `configs/docling.yaml`

Paper chunking enforced in corpus manifests and experiment manifests:

- `chunk_size: 512`
- `chunk_overlap: 100`

Docling enables OCR, table structure, and optional VLM image descriptions for indexing workflows.

## Model licenses

- EndoRAG code: Apache-2.0
- Third-party LLMs, embeddings, and rerankers: subject to their respective upstream licenses (Ollama model cards, Hugging Face model pages, OpenAI terms)
- MCQ datasets: included for research reproducibility with provenance metadata; respect original source restrictions

## Manifest index

| Manifest | Content |
| --- | --- |
| `data/manifests/documents.yaml` | External PDF archive URIs and counts |
| `data/manifests/vector_databases.yaml` | External Chroma archive URIs |
| `data/manifests/paper_results.json` | Inventory of 360 retained result JSON files |
| `data/manifests/paper_logs.json` | Inventory of 316 retained sanitized logs |
