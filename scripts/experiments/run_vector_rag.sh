#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."

DRY_RUN=()
if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN=(--dry-run)
fi

python -m endorag.cli evaluate --manifest configs/experiments/vector_rag_main.yaml "${DRY_RUN[@]}"
