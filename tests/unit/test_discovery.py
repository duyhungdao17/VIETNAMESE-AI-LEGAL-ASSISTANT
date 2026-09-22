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