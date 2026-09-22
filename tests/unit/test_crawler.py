from datetime import UTC, datetime

import httpx
import pytest

from legal_assistant.ingestion.crawler import CrawlService, SourceAccessBlocked
from legal_assistant.ingestion.models import SeedRecord
from legal_assistant.ingestion.storage import RawStore


def test_crawler_stores_html_and_published_pdf(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/document/123":
            return httpx.Response(
                200,
                text='<html><a class="original-file" href="/files/123.pdf">PDF</a></html>',
                headers={"content-type": "text/html"},
            )
        if request.url.path == "/files/123.pdf":
            return httpx.Response(200, content=b"%PDF-1.4", headers={"content-type": "application/pdf"})
        return httpx.Response(404)

    client = httpx.Client(base_url="https://example.test", transport=httpx.MockTransport(handler))
    crawler = CrawlService(client=client, store=RawStore(tmp_path / "raw"), source_identifier="vbpl")
    record = crawler.crawl(
        SeedRecord(
            source_id="vbpl:123",
            canonical_url="https://example.test/document/123",
            discovered_at=datetime(2026, 9, 22, tzinfo=UTC),
            selection_bucket="active",
        )
    )

    assert [artifact.kind.value for artifact in record.artifacts] == ["document_html", "original_pdf"]
    assert record.artifacts[1].source_url == "https://example.test/files/123.pdf"


def test_crawler_stops_on_forbidden_response(tmp_path) -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(403)))
    crawler = CrawlService(client=client, store=RawStore(tmp_path / "raw"), source_identifier="vbpl")

    with pytest.raises(SourceAccessBlocked):
        crawler.crawl(
            SeedRecord(
                source_id="vbpl:403",
                canonical_url="https://example.test/document/403",
                discovered_at=datetime(2026, 9, 22, tzinfo=UTC),
                selection_bucket="active",
            )
        )