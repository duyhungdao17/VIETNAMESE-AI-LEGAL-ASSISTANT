from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from time import sleep
from unicodedata import normalize
from urllib.parse import parse_qs, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from .crawler import SourceAccessBlocked, SourceScopeViolation, _origin, is_access_blocked
from .models import SeedRecord


class DiscoveryService:
    def __init__(self, client: httpx.Client, source_identifier: str, max_attempts: int = 3, request_delay_seconds: float = 0, sleep_fn: Callable[[float], None] = sleep) -> None:
        self.client = client
        self.source_identifier = source_identifier
        self.max_attempts = max_attempts
        self.request_delay_seconds = request_delay_seconds
        self.sleep_fn = sleep_fn

    def discover(self, listing_url: str, *, discovered_at: datetime) -> list[SeedRecord]:
        response = self._request("GET", listing_url)
        seeds: list[SeedRecord] = []
        seen: set[str] = set()
        origin = _origin(listing_url)
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

    def discover_api(self, listing_url: str, *, document_url_template: str, detail_url_template: str, discovered_at: datetime, limit: int) -> list[SeedRecord]:
        response = self._request("POST", listing_url, json={"pageSize": limit, "pageNumber": 1, "sortDirection": "desc", "sortBy": "id"})
        items = response.json().get("data", {}).get("items", [])
        if not isinstance(items, list):
            raise ValueError("API listing response has no data.items list")
        records: list[SeedRecord] = []
        for item in items[:limit]:
            doc_id = item.get("id")
            if doc_id is None:
                continue
            normalized_id = str(doc_id)
            status = item.get("effStatus")
            status_label = status.get("name") if isinstance(status, dict) else None
            records.append(SeedRecord(source_id=f"{self.source_identifier}:{normalized_id}", canonical_url=document_url_template.format(doc_id=normalized_id), api_detail_url=detail_url_template.format(doc_id=normalized_id), discovered_at=discovered_at, source_status_label=status_label, selection_bucket=_selection_bucket(status_label), selection_reason="api_listing"))
        return records

    def _request(self, method: str, url: str, **kwargs: object) -> httpx.Response:
        origin = _origin(url)
        for attempt in range(self.max_attempts):
            try:
                response = self.client.request(method, url, follow_redirects=True, **kwargs)
            except httpx.HTTPError:
                if attempt + 1 == self.max_attempts:
                    raise
                self.sleep_fn(2**attempt)
                continue
            if self.request_delay_seconds:
                self.sleep_fn(self.request_delay_seconds)
            if _origin(str(response.url)) != origin:
                raise SourceScopeViolation(f"out-of-scope redirect: {response.url}")
            if is_access_blocked(response):
                raise SourceAccessBlocked(f"source access blocked: {response.status_code} {url}")
            if response.status_code == 429:
                if attempt + 1 == self.max_attempts:
                    raise SourceAccessBlocked(f"source rate limited: {response.status_code} {url}")
                retry_after = response.headers.get("retry-after")
                try:
                    retry_delay = float(retry_after) if retry_after is not None else 2**attempt
                except ValueError:
                    retry_delay = 2**attempt
                self.sleep_fn(retry_delay)
                continue
            if response.status_code in {408, 500, 502, 503, 504} and attempt + 1 < self.max_attempts:
                self.sleep_fn(2**attempt)
                continue
            response.raise_for_status()
            return response
        raise RuntimeError("unreachable")


def _selection_bucket(status_label: str | None) -> str:
    if status_label is None:
        return "unclassified"
    status = normalize("NFD", status_label.casefold()).encode("ascii", "ignore").decode("ascii")
    if "het hieu luc toan bo" in status:
        return "expired"
    if "het hieu luc mot phan" in status:
        return "partially_expired"
    if "chua co hieu luc" in status:
        return "future"
    if "con hieu luc" in status:
        return "active"
    return "unclassified"