import importlib.util
from datetime import UTC, datetime

import httpx
import pytest

from legal_assistant.ingestion.gateway import VBPLGatewaySyncService
from legal_assistant.ingestion.models import DatasetManifest
from legal_assistant.ingestion.storage import RawStore


ALLOWED = {"Còn hiệu lực", "Hết hiệu lực một phần", "Chưa có hiệu lực"}


def _manifest() -> DatasetManifest:
    return DatasetManifest(
        dataset_version="vbpl-gateway-pilot-v1",
        schema_version="2",
        config_version="2",
        source_identifier="vbpl_gateway",
        source_base_url="https://example.test/api",
        seed_sha256="config-hash",
    )


def test_gateway_sync_service_is_available() -> None:
    spec = importlib.util.find_spec("legal_assistant.ingestion.gateway")

    assert spec is not None


def test_gateway_sync_stores_list_artifact_and_fetches_only_selected_statuses(tmp_path) -> None:
    statuses = ["Còn hiệu lực", "Hết hiệu lực một phần", "Chưa có hiệu lực", "Hết hiệu lực toàn bộ"]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            assert request.url.path == "/api/qtdc/public/doc/all"
            return httpx.Response(200, json={"data": {"total": 20, "pageNumber": 1, "pageSize": 10, "items": [{"id": index + 1, "effStatus": {"name": status}, "document_url": f"https://example.test/doc/{index + 1}"} for index, status in enumerate(statuses)]}})
        document_id = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json={"data": {"documentContent": {"content": f"<article>{document_id}</article>"}}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    service = VBPLGatewaySyncService(client=client, store=RawStore(tmp_path / "raw"), api_base_url="https://example.test/api", request_delay_seconds=0)
    manifest = _manifest()

    service.sync(manifest, max_pages=1)

    assert [record.source_id for record in manifest.records] == ["vbpl:1", "vbpl:2", "vbpl:3"]
    assert [artifact.kind.value for artifact in manifest.discovery_artifacts] == ["discovery_json"]
    assert manifest.status_counts == {status: 1 for status in statuses}
    assert manifest.skipped_status_counts == {"Hết hiệu lực toàn bộ": 1}
    assert manifest.checkpoint.last_completed_page == 1
    assert manifest.checkpoint.observed_total == 20
    assert manifest.discovery_complete is False
    assert manifest.discovery_incomplete is False
    assert all(record.artifacts[0].kind.value == "document_json" for record in manifest.records)

def test_gateway_sync_stops_when_a_selected_document_is_blocked(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "total": 1,
                        "pageNumber": 1,
                        "pageSize": 10,
                        "items": [{"id": 1, "effStatus": {"name": "Còn hiệu lực"}}],
                    }
                },
            )
        return httpx.Response(403)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    service = VBPLGatewaySyncService(
        client=client,
        store=RawStore(tmp_path / "raw"),
        api_base_url="https://example.test/api",
        request_delay_seconds=0,
    )

    from legal_assistant.ingestion.crawler import SourceAccessBlocked

    with pytest.raises(SourceAccessBlocked):
        service.sync(_manifest(), max_pages=1)