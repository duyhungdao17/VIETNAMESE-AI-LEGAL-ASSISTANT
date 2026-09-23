from datetime import UTC, datetime

from legal_assistant.ingestion.models import (
    ArtifactKind,
    CrawlCheckpoint,
    CrawlRecord,
    DatasetManifest,
    RawArtifact,
)


def test_manifest_preserves_artifact_provenance_and_dedup_reference() -> None:
    fetched_at = datetime(2026, 9, 22, 1, 2, 3, tzinfo=UTC)
    artifact = RawArtifact.from_bytes(
        artifact_id="artifact-html-1",
        kind=ArtifactKind.DOCUMENT_HTML,
        source_id="vbpl:123",
        source_url="https://example.test/document/123",
        fetched_at=fetched_at,
        payload=b"<html>legal text</html>",
        relative_path="artifacts/abc.html",
        media_type="text/html",
        http_status=200,
    )
    record = CrawlRecord(
        source_id="vbpl:123",
        canonical_url="https://example.test/document/123",
        artifacts=[artifact],
        deduplicates_artifact_id="artifact-prior",
    )
    manifest = DatasetManifest(
        dataset_version="vbpl-central-pilot-v1",
        schema_version="1",
        config_version="1",
        source_identifier="vbpl",
        source_base_url="https://example.test",
        seed_sha256="a" * 64,
        records=[record],
        checkpoint=CrawlCheckpoint(completed_source_ids=["vbpl:123"]),
    )

    assert artifact.sha256 == "808e597de752ae431108f649c8c2071dfb3cfbd79ed14e253c4174b5b3e7049c"
    assert manifest.records[0].artifacts[0].source_url == "https://example.test/document/123"
    assert manifest.records[0].deduplicates_artifact_id == "artifact-prior"
    assert manifest.checkpoint.completed_source_ids == ["vbpl:123"]

def test_source_profile_requires_api_endpoints_for_hybrid_mode() -> None:
    from legal_assistant.ingestion import models

    profile = models.SourceProfile(
        source_identifier="vbpl",
        mode="hybrid",
        api_list_url="https://api.example.test/documents",
        api_detail_url_template="https://api.example.test/doc/{doc_id}",
        document_url_template="https://www.example.test/doc/{doc_id}",
    )

    assert profile.request_delay_seconds == 1.5

def test_manifest_tracks_gateway_discovery_state() -> None:
    manifest = DatasetManifest(
        dataset_version="vbpl-gateway-pilot-v1",
        schema_version="2",
        config_version="2",
        source_identifier="vbpl_gateway",
        source_base_url="https://example.test/api",
        seed_sha256="config-hash",
    )

    assert manifest.discovery_artifacts == []
    assert manifest.status_counts == {}
    assert manifest.discovery_complete is False
    assert manifest.discovery_incomplete is False
    assert manifest.checkpoint.last_completed_page == 0