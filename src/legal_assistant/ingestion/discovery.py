from __future__ import annotations

from datetime import datetime
from urllib.parse import parse_qs, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from .crawler import SourceAccessBlocked, SourceScopeViolation, _origin
from .models import SeedRecord


class DiscoveryService:
    def __init__(self, client: httpx.Client, source_identifier: str) -> None:
        self.client = client
        self.source_identifier = source_identifier

    def discover(self, listing_url: str, *, discovered_at: datetime) -> list[SeedRecord]:
        origin = _origin(listing_url)
        response = self.client.get(listing_url, follow_redirects=True)
        if _origin(str(response.url)) != origin:
            raise SourceScopeViolation(f"out-of-scope redirect: {response.url}")
        if response.status_code in {401, 403} or b"captcha" in response.content.lower():
            raise SourceAccessBlocked(f"source access blocked: {response.status_code} {listing_url}")
        response.raise_for_status()
        seeds: list[SeedRecord] = []
        seen: set[str] = set()
        for link in BeautifulSoup(response.text, "lxml").select("a[href*='vbpq-toanvan'], a[href*='ItemID=']"):
            url = urljoin(str(response.url), link["href"])
            if _origin(url) != origin:
                continue
            item_id = parse_qs(urlparse(url).query).get("ItemID", [None])[0]
            if item_id is None or item_id in seen:
                continue
            seen.add(item_id)
            seeds.append(SeedRecord(source_id=f"{self.source_identifier}:{item_id}", canonical_url=url, discovered_at=discovered_at, selection_bucket="unclassified", selection_reason="listing_link"))
        return seeds