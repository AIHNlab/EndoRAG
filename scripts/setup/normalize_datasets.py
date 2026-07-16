#!/usr/bin/env python3
"""Add stable IDs to authorized MCQ datasets without changing question content."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DATASETS_DIR = REPO_ROOT / "data" / "datasets"

# filename -> (slug, expected_count)
DATASET_SPECS: dict[str, tuple[str, int]] = {
    "MCQs_sample_questions2015_full.json": ("diabetes", 53),
    "ThyroidGland_dataset.json": ("thyroid", 59),
    "ParathyroidGlandAndBoneDisease_dataset.json": ("parathyroid", 43),
    "PituitaryGlandAndHypothalamus_dataset.json": ("pituitary", 54),
    "AdrenalGlands_dataset.json": ("adrenal", 50),
    "ReproductiveEndocrinology_dataset.json": ("reproductive", 42),
    "UKEU.json": ("ukeu", 85),
}


def _load_records(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"Dataset must be a JSON list: {path}")
    return payload


def _expected_id(slug: str, index: int) -> str:
    return f"{slug}:{index:03d}"


def normalize_file(path: Path, *, slug: str, expected_count: int, write: bool) -> list[str]:
    errors: list[str] = []
    records = _load_records(path)
    if len(records) != expected_count:
        errors.append(f"{path.name}: expected {expected_count} records, found {len(records)}")

    changed = False
    for index, record in enumerate(records, start=1):
        expected = _expected_id(slug, index)
        current = record.get("id")
        if current != expected:
            if current is not None:
                errors.append(f"{path.name}[{index}]: id {current!r} != expected {expected!r}")
            record["id"] = expected
            changed = True

    if write and changed:
        path.write_text(
            json.dumps(records, indent=4, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    return errors


def check_all() -> dict[str, int]:
    counts: dict[str, int] = {}
    errors: list[str] = []
    found = {path.name for path in DATASETS_DIR.glob("*.json")}
    expected_files = set(DATASET_SPECS)
    if found != expected_files:
        missing = sorted(expected_files - found)
        extra = sorted(found - expected_files)
        if missing:
            errors.append(f"Missing dataset files: {', '.join(missing)}")
        if extra:
            errors.append(f"Unexpected dataset files: {', '.join(extra)}")

    for filename, (slug, expected_count) in sorted(DATASET_SPECS.items()):
        path = DATASETS_DIR / filename
        if not path.is_file():
            continue
        records = _load_records(path)
        counts[filename] = len(records)
        if len(records) != expected_count:
            errors.append(f"{filename}: expected {expected_count}, found {len(records)}")
        for index, record in enumerate(records, start=1):
            if record.get("id") != _expected_id(slug, index):
                errors.append(
                    f"{filename}[{index}]: missing or incorrect id "
                    f"(expected {_expected_id(slug, index)!r})"
                )

    if sum(counts.values()) != 386:
        errors.append(f"Total record count {sum(counts.values())} != 386")

    if errors:
        print("Dataset validation failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        raise SystemExit(1)

    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate dataset counts and stable IDs without writing files.",
    )
    args = parser.parse_args(argv)

    if args.check:
        counts = check_all()
        print(counts)
        return 0

    errors: list[str] = []
    for filename, (slug, expected_count) in sorted(DATASET_SPECS.items()):
        path = DATASETS_DIR / filename
        if not path.is_file():
            errors.append(f"Missing dataset file: {filename}")
            continue
        errors.extend(normalize_file(path, slug=slug, expected_count=expected_count, write=True))

    if errors:
        print("Dataset normalization failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    counts = check_all()
    print(f"Normalized {len(counts)} datasets ({sum(counts.values())} records).")
    print(counts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
