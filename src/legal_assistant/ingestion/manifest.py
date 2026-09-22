from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from .models import DatasetManifest


MANIFEST_NAME = "manifest.json"


def write_manifest(dataset_root: Path, manifest: DatasetManifest) -> Path:
    dataset_root.mkdir(parents=True, exist_ok=True)
    target = dataset_root / MANIFEST_NAME
    temporary = dataset_root / f".{MANIFEST_NAME}.{uuid4().hex}.tmp"
    temporary.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    temporary.replace(target)
    return target


def load_manifest(dataset_root: Path) -> DatasetManifest:
    return DatasetManifest.model_validate_json((dataset_root / MANIFEST_NAME).read_text(encoding="utf-8"))