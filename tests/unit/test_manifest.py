from datetime import UTC, datetime

from legal_assistant.ingestion.manifest import load_manifest, write_manifest
from legal_assistant.ingestion.models import CrawlCheckpoint, DatasetManifest


def test_manifest_round_trips_atomically(tmp_path) -> None:
    manifest = DatasetManifest(
        dataset_version="pilot-v1",
        schema_version="1",
        config_version="1",
        source_identifier="vbpl",
        source_base_url="https://example.test",
        seed_sha256="b" * 64,
        checkpoint=CrawlCheckpoint(completed_source_ids=["vbpl:1"]),
    )

    write_manifest(tmp_path, manifest)
    loaded = load_manifest(tmp_path)

    assert loaded.dataset_version == "pilot-v1"
    assert loaded.checkpoint.completed_source_ids == ["vbpl:1"]
    assert not list(tmp_path.glob("*.tmp"))