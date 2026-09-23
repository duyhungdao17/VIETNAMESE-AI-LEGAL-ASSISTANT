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

def test_crawler_prefers_api_detail_and_stores_raw_json(tmp_path) -> None:
    api_payload = {"data": {"documentContent": {"content": "<article>API document</article>"}}}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/doc/123":
            return httpx.Response(200, json=api_payload)
        raise AssertionError(f"unexpected request: {request.url}")

    client = httpx.Client(base_url="https://example.test", transport=httpx.MockTransport(handler))
    crawler = CrawlService(client=client, store=RawStore(tmp_path / "raw"), source_identifier="vbpl")
    seed = SeedRecord(
        source_id="vbpl:123",
        canonical_url="https://example.test/doc/123",
        api_detail_url="https://example.test/api/doc/123",
        discovered_at=datetime(2026, 9, 22, tzinfo=UTC),
        selection_bucket="active",
    )

    record = crawler.crawl_api(seed)

    assert [artifact.kind.value for artifact in record.artifacts] == ["document_json", "document_html"]
    assert record.artifacts[0].source_url == "https://example.test/api/doc/123"
    assert (tmp_path / "raw" / record.artifacts[0].relative_path).read_bytes() == b'{"data":{"documentContent":{"content":"<article>API document</article>"}}}'


def test_crawler_uses_document_html_when_api_detail_has_no_content(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/doc/123":
            return httpx.Response(200, json={"data": {"documentContent": {}}})
        if request.url.path == "/doc/123":
            return httpx.Response(200, text="<article>HTML fallback</article>")
        raise AssertionError(f"unexpected request: {request.url}")

    client = httpx.Client(base_url="https://example.test", transport=httpx.MockTransport(handler))
    crawler = CrawlService(client=client, store=RawStore(tmp_path / "raw"), source_identifier="vbpl")
    seed = SeedRecord(
        source_id="vbpl:123",
        canonical_url="https://example.test/doc/123",
        api_detail_url="https://example.test/api/doc/123",
        discovered_at=datetime(2026, 9, 22, tzinfo=UTC),
        selection_bucket="active",
    )

    record = crawler.crawl_api(seed)

    assert [artifact.kind.value for artifact in record.artifacts] == ["document_json", "document_html"]
    assert record.artifacts[1].source_url == "https://example.test/doc/123"

def test_crawler_applies_configured_delay_after_a_request(tmp_path) -> None:
    delays: list[float] = []
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, text="<article>document</article>")))
    crawler = CrawlService(
        client=client,
        store=RawStore(tmp_path / "raw"),
        source_identifier="vbpl",
        request_delay_seconds=1.5,
        sleep_fn=delays.append,
    )

    crawler.crawl(SeedRecord(source_id="vbpl:123", canonical_url="https://example.test/doc/123", discovered_at=datetime(2026, 9, 22, tzinfo=UTC), selection_bucket="active"))

    assert delays == [1.5]

def test_crawler_stops_after_bounded_rate_limit_retries(tmp_path) -> None:
    delays: list[float] = []
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(429, headers={"retry-after": "3"})))
    crawler = CrawlService(
        client=client,
        store=RawStore(tmp_path / "raw"),
        source_identifier="vbpl",
        max_attempts=2,
        sleep_fn=delays.append,
    )

    with pytest.raises(SourceAccessBlocked, match="source rate limited"):
        crawler.crawl(SeedRecord(source_id="vbpl:429", canonical_url="https://example.test/doc/429", discovered_at=datetime(2026, 9, 22, tzinfo=UTC), selection_bucket="active"))

    assert delays == [3.0]