#!/usr/bin/env python3
"""Verify external document and vector-database artifacts declared in manifests."""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_DIR = REPO_ROOT / "data" / "manifests"
MANIFEST_FILES = (
    MANIFEST_DIR / "documents.yaml",
    MANIFEST_DIR / "vector_databases.yaml",
)
ENV_KEYS = ("ENDORAG_DOCUMENT_ROOT", "ENDORAG_CHROMA_ROOT", "ENDORAG_ARTIFACT_BASE_URL")


def _load_dotenv() -> Path | None:
    """Load repo `.env` into the process environment if present.

    Does not override variables already set in the shell.
    """
    env_path = REPO_ROOT / ".env"
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
        os.environ[key] = value
    return env_path


def _expand(value: Any) -> Any:
    if isinstance(value, str):
        return os.path.expandvars(value)
    if isinstance(value, list):
        return [_expand(item) for item in value]
    if isinstance(value, dict):
        return {key: _expand(item) for key, item in value.items()}
    return value


def _unresolved_env_refs(text: str) -> list[str]:
    return [key for key in ENV_KEYS if f"${{{key}}}" in text or f"${key}" in text]


def _load_manifest(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Manifest must be a mapping: {path}")
    return _expand(payload)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_placeholder_sha(value: object) -> bool:
    text = str(value or "").strip()
    return not text or text == "TODO_FILL_AFTER_UPLOAD"


def _verify_record(
    record: dict[str, Any],
    *,
    manifest_name: str,
    require_paths: bool,
) -> list[str]:
    errors: list[str] = []
    logical_name = str(record.get("logical_name", "<unnamed>"))
    required_fields = (
        "logical_name",
        "artifact_uri",
        "relative_path",
        "sha256",
        "category",
    )
    for field in required_fields:
        if field not in record:
            errors.append(f"{manifest_name}:{logical_name}: missing field {field!r}")

    expected_path = record.get("expected_path")
    if expected_path:
        expected = str(expected_path)
        unresolved = _unresolved_env_refs(expected)
        if unresolved:
            errors.append(
                f"{manifest_name}:{logical_name}: unresolved env var(s) "
                f"{', '.join(unresolved)} in path {expected!r}. "
                f"Create {REPO_ROOT / '.env'} or export them in the shell. "
                "ENDORAG_CHROMA_ROOT must be the chroma_db root "
                "(e.g. .../chroma_db), not .../qwen3-embedding:8b/chunk_512."
            )
        else:
            path = Path(expected)
            if require_paths and not path.exists():
                errors.append(f"{manifest_name}:{logical_name}: expected path missing: {path}")
    elif require_paths:
        errors.append(f"{manifest_name}:{logical_name}: expected_path is required when checking paths")

    sha256 = record.get("sha256")
    artifact_uri = str(record.get("artifact_uri", ""))
    if not _is_placeholder_sha(sha256):
        archive = Path(artifact_uri)
        if archive.is_file():
            actual = _sha256_file(archive)
            if actual != str(sha256).strip().lower():
                errors.append(
                    f"{manifest_name}:{logical_name}: sha256 mismatch for local archive {archive}"
                )
        elif require_paths:
            errors.append(
                f"{manifest_name}:{logical_name}: sha256 set but local archive not found: {archive}"
            )

    base_url = os.getenv("ENDORAG_ARTIFACT_BASE_URL", "").strip()
    if base_url and artifact_uri.startswith(base_url):
        relative = artifact_uri[len(base_url.rstrip("/")) :].lstrip("/")
        if not relative:
            errors.append(f"{manifest_name}:{logical_name}: artifact_uri has no relative path")

    return errors


def verify(*, require_paths: bool) -> dict[str, Any]:
    report: dict[str, Any] = {
        "manifests_checked": [],
        "artifact_count": 0,
        "placeholder_sha256_count": 0,
        "missing_paths": [],
        "errors": [],
    }

    for manifest_path in MANIFEST_FILES:
        if not manifest_path.is_file():
            report["errors"].append(f"Missing manifest: {manifest_path}")
            continue

        payload = _load_manifest(manifest_path)
        artifacts = payload.get("artifacts") or []
        if not isinstance(artifacts, list) or not artifacts:
            report["errors"].append(f"{manifest_path.name}: artifacts list is empty")
            continue

        report["manifests_checked"].append(manifest_path.name)
        for record in artifacts:
            if not isinstance(record, dict):
                report["errors"].append(f"{manifest_path.name}: artifact entry must be a mapping")
                continue
            report["artifact_count"] += 1
            if _is_placeholder_sha(record.get("sha256")):
                report["placeholder_sha256_count"] += 1

            expected_path = record.get("expected_path")
            if require_paths and expected_path and not Path(str(expected_path)).exists():
                report["missing_paths"].append(
                    {
                        "manifest": manifest_path.name,
                        "logical_name": record.get("logical_name"),
                        "expected_path": str(expected_path),
                    }
                )

            report["errors"].extend(
                _verify_record(record, manifest_name=manifest_path.name, require_paths=require_paths)
            )

    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate manifest schema only; do not require local artifact paths.",
    )
    parser.add_argument(
        "--require-paths",
        action="store_true",
        help="Require ENDORAG_DOCUMENT_ROOT / ENDORAG_CHROMA_ROOT paths to exist.",
    )
    args = parser.parse_args(argv)

    loaded_env = _load_dotenv()
    if loaded_env is not None:
        print(f"Loaded environment from {loaded_env}")
    elif args.require_paths and not args.check:
        print(
            f"Warning: {REPO_ROOT / '.env'} not found. "
            "Export ENDORAG_DOCUMENT_ROOT and ENDORAG_CHROMA_ROOT, or copy "
            ".env.example to .env and edit the paths.",
            file=sys.stderr,
        )

    for key in ("ENDORAG_DOCUMENT_ROOT", "ENDORAG_CHROMA_ROOT"):
        value = os.getenv(key, "").strip()
        if args.require_paths and not args.check:
            print(f"{key}={value or '<unset>'}")

    require_paths = args.require_paths and not args.check
    report = verify(require_paths=require_paths)

    print(f"Manifests checked: {', '.join(report['manifests_checked']) or 'none'}")
    print(f"Artifacts: {report['artifact_count']}")
    print(f"Placeholder SHA-256 entries: {report['placeholder_sha256_count']}")

    if report["missing_paths"]:
        print("Missing expected paths:")
        for item in report["missing_paths"]:
            print(
                f"  - {item['manifest']}:{item['logical_name']} -> {item['expected_path']}"
            )

    if report["errors"]:
        print("Verification errors:", file=sys.stderr)
        for error in report["errors"]:
            print(f"  - {error}", file=sys.stderr)
        return 1

    if args.check and report["placeholder_sha256_count"]:
        print(
            "Note: SHA-256 placeholders remain until external archives are published "
            "(see manifest checksum_policy)."
        )

    base_url = os.getenv("ENDORAG_ARTIFACT_BASE_URL", "").strip()
    if base_url:
        sample = urljoin(base_url.rstrip("/") + "/", "documents/domain_specific/diabetes_literature.tar.gz")
        print(f"Artifact base URL resolved: {base_url}")
        print(f"Sample URI: {sample}")

    print("External artifact manifests are valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
