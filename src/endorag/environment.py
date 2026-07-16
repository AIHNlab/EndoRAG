"""Capture the software and hardware context of an experiment."""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

PACKAGES = (
    "chromadb",
    "docling",
    "docling-core",
    "langgraph",
    "llama-index",
    "pydantic",
    "pydantic-ai-slim",
    "torch",
    "transformers",
)


def _package_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for package in PACKAGES:
        try:
            versions[package] = version(package)
        except PackageNotFoundError:
            versions[package] = "not-installed"
    return versions


def _ollama_version() -> str | None:
    try:
        result = subprocess.run(
            ["ollama", "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    value = (result.stdout or result.stderr).strip()
    return value or None


def _cuda_report() -> dict[str, object]:
    try:
        import torch
    except ImportError:
        return {"available": False, "devices": []}
    available = torch.cuda.is_available()
    return {
        "available": available,
        "runtime": getattr(torch.version, "cuda", None),
        "devices": [
            torch.cuda.get_device_name(index)
            for index in range(torch.cuda.device_count())
        ]
        if available
        else [],
    }


def write_environment_report(path: str | Path) -> dict[str, object]:
    report: dict[str, object] = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "python": sys.version,
        "platform": platform.platform(),
        "packages": _package_versions(),
        "cuda": _cuda_report(),
        "ollama": _ollama_version(),
        "configured_environment_variables": sorted(
            name
            for name in os.environ
            if name.startswith("ENDORAG_")
            or name in {"OLLAMA_API_KEY", "OPENAI_API_KEY", "RERANK_DEVICE"}
        ),
    }
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(report, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return report
