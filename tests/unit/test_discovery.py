from datetime import UTC, datetime

import httpx

from legal_assistant.ingestion.discovery import DiscoveryService


def test_discovery_writes_unique_seed_records_from_document_links() -> None:
    html = '''<a href="/TW/Pages/vbpq-toanvan.aspx?ItemID=123">One</a>
              <a href="/TW/Pages/vbpq-toanvan.aspx?ItemID=123">Duplicate</a>
              <a href="/TW/Pages/vbpq-toanvan.aspx?ItemID=456">Two</a>'''
    client = httpx.Client(base_url="https://example.test", transport=httpx.MockTransport(lambda request: httpx.Response(200, text=html)))

    records = DiscoveryService(client, "vbpl").discover("https://example.test/listing", discovered_at=datetime(2026, 9, 22, tzinfo=UTC))

    assert [record.source_id for record in records] == ["vbpl:123", "vbpl:456"]
    assert all(record.selection_bucket == "unclassified" for record in records)

def test_discovery_accepts_a_valid_listing_that_mentions_captcha() -> None:
    html = '''<script>window.captchaConfiguration = {};</script>
              <a href="/TW/Pages/vbpq-toanvan.aspx?ItemID=123">One</a>'''
    client = httpx.Client(
        base_url="https://example.test",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=html)),
    )

    records = DiscoveryService(client, "vbpl").discover(
        "https://example.test/listing",
        discovered_at=datetime(2026, 9, 22, tzinfo=UTC),
    )

    assert [record.source_id for record in records] == ["vbpl:123"]

def test_discovery_from_api_keeps_source_status_and_detail_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/documents"
        return httpx.Response(
            200,
            json={
                "data": {
                    "items": [
                        {"id": "123", "effStatus": {"name": "Còn hiệu lực"}},
                        {"id": "456", "effStatus": {"name": "Hết hiệu lực toàn bộ"}},
                    ]
                }
            },
        )

    client = httpx.Client(base_url="https://example.test", transport=httpx.MockTransport(handler))

    records = DiscoveryService(client, "vbpl").discover_api(
        "https://example.test/api/documents",
        document_url_template="https://example.test/doc/{doc_id}",
        detail_url_template="https://example.test/api/doc/{doc_id}",
        discovered_at=datetime(2026, 9, 22, tzinfo=UTC),
        limit=10,
    )

    assert [(record.source_id, record.source_status_label, record.selection_bucket) for record in records] == [
        ("vbpl:123", "Còn hiệu lực", "active"),
        ("vbpl:456", "Hết hiệu lực toàn bộ", "expired"),
    ]
    assert records[0].api_detail_url == "https://example.test/api/doc/123"

def test_api_discovery_applies_configured_delay() -> None:
    delays: list[float] = []
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"data": {"items": []}})))
    discovery = DiscoveryService(client, "vbpl", request_delay_seconds=1.5, sleep_fn=delays.append)

    records = discovery.discover_api(
        "https://example.test/api/documents",
        document_url_template="https://example.test/doc/{doc_id}",
        detail_url_template="https://example.test/api/doc/{doc_id}",
        discovered_at=datetime(2026, 9, 22, tzinfo=UTC),
        limit=10,
    )

    assert records == []
    assert delays == [1.5]