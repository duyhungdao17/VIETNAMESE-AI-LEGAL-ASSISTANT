from datetime import UTC, datetime

import httpx
import pytest

from legal_assistant.ingestion.crawler import CrawlService, SourceScopeViolation
from legal_assistant.ingestion.discovery import DiscoveryService
from legal_assistant.ingestion.models import SeedRecord
from legal_assistant.ingestion.storage import RawStore
from legal_assistant.ingestion.crawler import SourceAccessBlocked


def test_discovery_stops_on_forbidden_response() -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(403)))
    with pytest.raises(SourceAccessBlocked):
        DiscoveryService(client, "vbpl").discover("https://example.test/listing", discovered_at=datetime(2026, 9, 22, tzinfo=UTC))


def test_crawler_rejects_pdf_link_outside_seed_origin(tmp_path) -> None:
    html = '<a class="original-file" href="https://other.test/file.pdf">PDF</a>'
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, text=html)))
    crawler = CrawlService(client=client, store=RawStore(tmp_path / "raw"), source_identifier="vbpl")
    seed = SeedRecord(source_id="vbpl:1", canonical_url="https://example.test/document/1", discovered_at=datetime(2026, 9, 22, tzinfo=UTC), selection_bucket="active")

    with pytest.raises(SourceScopeViolation):
        crawler.crawl(seed)