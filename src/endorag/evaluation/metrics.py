"""Evaluation metrics shared by all retained methods."""

from __future__ import annotations

import re


def normalize_mcq_letter(value: object) -> str:
    text = str(value or "").strip().lower()
    if text in {"a", "b", "c", "d", "e"}:
        return text
    match = re.search(r"(?:answer\s*[:\-]\s*)?\b([a-e])\b", text)
    return match.group(1) if match else ""


def exact_match(actual: object, expected: object) -> float:
    return float(normalize_mcq_letter(actual) == normalize_mcq_letter(expected))
