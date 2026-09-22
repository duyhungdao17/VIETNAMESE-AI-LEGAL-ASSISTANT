import hashlib

import pytest

from legal_assistant.cli import DatasetCompatibilityError, prepare_manifest
from legal_assistant.ingestion.manifest import write_manifest
from legal_assistant.ingestion.models import DatasetManifest


def test_existing_dataset_requires_compatible_resume(tmp_path) -> None:
    root = tmp_path / "pilot-v1"
    existing = DatasetManifest(dataset_version="pilot-v1", schema_version="1", config_version="1", source_identifier="vbpl", source_base_url="https://example.test", seed_sha256=hashlib.sha256(b"same").hexdigest())
    write_manifest(root, existing)

    with pytest.raises(DatasetCompatibilityError):
        prepare_manifest(root, "pilot-v1", "https://example.test", b"same", resume=False)
    with pytest.raises(DatasetCompatibilityError):
        prepare_manifest(root, "pilot-v1", "https://example.test", b"different", resume=True)

    resumed = prepare_manifest(root, "pilot-v1", "https://example.test", b"same", resume=True)
    assert resumed.seed_sha256 == hashlib.sha256(b"same").hexdigest()

def test_dataset_directory_without_manifest_fails_closed(tmp_path) -> None:
    root = tmp_path / "orphaned-v1"
    root.mkdir()
    (root / "artifact.html").write_bytes(b"untracked raw data")

    with pytest.raises(DatasetCompatibilityError):
        prepare_manifest(root, "orphaned-v1", "https://example.test", b"seed", resume=False)