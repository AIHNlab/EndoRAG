"""Load repository `.env` into process environment."""

from __future__ import annotations

import os
from pathlib import Path


def load_repo_dotenv(repo_root: str | Path | None = None) -> Path | None:
    """Load `.env` from the EndoRAG repo root.

    Existing environment variables are not overwritten.
    Returns the loaded path, or None if no `.env` exists.
    """
    if repo_root is None:
        # src/endorag/dotenv_loader.py -> repo root is parents[2]
        root = Path(__file__).resolve().parents[2]
    else:
        root = Path(repo_root)
    env_path = root / ".env"
    if not env_path.is_file():
        return None
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ[key] = value.strip()
    return env_path
