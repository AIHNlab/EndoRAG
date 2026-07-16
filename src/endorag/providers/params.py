"""Parsing helpers for model parameter overrides."""

from __future__ import annotations

import ast
from typing import Any


def str2any(value: str) -> bool | int | float | str:
    """Convert a command-line scalar to its natural Python type."""
    lowered = value.lower()
    if lowered in {"yes", "true"}:
        return True
    if lowered in {"no", "false"}:
        return False
    try:
        return int(value)
    except ValueError:
        try:
            return float(value)
        except ValueError:
            return value


def get_params(params: list[list[str]] | None) -> dict[str, Any]:
    """Convert ``[[name, value...], ...]`` command-line overrides to a mapping."""
    parsed: dict[str, Any] = {}
    for param_name, *param_values in params or []:
        if not param_values:
            raise ValueError(f"Parameter {param_name!r} has no value")
        if param_name == "evaluation_steps" or "kwargs" in param_name:
            parsed[param_name] = ast.literal_eval(" ".join(param_values))
        elif len(param_values) > 1:
            parsed[param_name] = [str2any(value) for value in param_values]
        else:
            parsed[param_name] = str2any(param_values[0])
    return parsed
