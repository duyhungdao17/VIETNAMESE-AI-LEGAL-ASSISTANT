from datetime import UTC, datetime

from legal_assistant.ingestion.models import ArtifactKind
from legal_assistant.ingestion.storage import RawStore


def test_raw_store_reuses_identical_content_without_overwrite(tmp_path) -> None:
    store = RawStore(tmp_path / "vbpl-central-pilot-v1")
    first, first_dedup = store.persist_bytes(
        kind=ArtifactKind.DOCUMENT_HTML,
        source_id="vbpl:123",
        source_url="https://example.test/123",
        fetched_at=datetime(2026, 9, 22, tzinfo=UTC),
        payload=b"source response",
        media_type="text/html",
        http_status=200,
        suffix=".html",
    )
    second, second_dedup = store.persist_bytes(
        kind=ArtifactKind.DOCUMENT_HTML,
        source_id="vbpl:456",
        source_url="https://example.test/456",
        fetched_at=datetime(2026, 9, 22, tzinfo=UTC),
        payload=b"source response",
        media_type="text/html",
        http_status=200,
        suffix=".html",
    )

    assert first_dedup is False
    assert second_dedup is True
    assert first.relative_path == second.relative_path
    assert (store.dataset_root / first.relative_path).read_bytes() == b"source response"