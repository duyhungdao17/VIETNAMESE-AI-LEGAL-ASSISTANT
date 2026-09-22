from __future__ import annotations

from datetime import UTC, datetime
from time import sleep
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from .models import ArtifactKind, CrawlRecord, SeedRecord
from .storage import RawStore


class SourceAccessBlocked(RuntimeError):
    pass


class SourceScopeViolation(RuntimeError):
    pass


class CrawlService:
    def __init__(self, *, client: httpx.Client, store: RawStore, source_identifier: str, max_attempts: int = 3) -> None:
        self.client = client
        self.store = store
        self.source_identifier = source_identifier
        self.max_attempts = max_attempts

    def crawl(self, seed: SeedRecord) -> CrawlRecord:
        origin = _origin(seed.canonical_url)
        response = self._get(seed.canonical_url, origin)
        fetched_at = datetime.now(UTC)
        html_artifact, _ = self.store.persist_bytes(kind=ArtifactKind.DOCUMENT_HTML, source_id=seed.source_id, source_url=str(response.url), fetched_at=fetched_at, payload=response.content, media_type=response.headers.get("content-type", "text/html"), http_status=response.status_code, suffix=".html")
        record = CrawlRecord(source_id=seed.source_id, canonical_url=seed.canonical_url, artifacts=[html_artifact], source_status_label=seed.source_status_label, selection_bucket=seed.selection_bucket)
        for pdf_url in self._published_pdf_urls(response.text, str(response.url)):
            pdf_response = self._get(pdf_url, origin)
            pdf_artifact, deduplicated = self.store.persist_bytes(kind=ArtifactKind.ORIGINAL_PDF, source_id=seed.source_id, source_url=str(pdf_response.url), fetched_at=datetime.now(UTC), payload=pdf_response.content, media_type=pdf_response.headers.get("content-type", "application/pdf"), http_status=pdf_response.status_code, suffix=".pdf")
            record.artifacts.append(pdf_artifact)
            if deduplicated:
                record.deduplicates_artifact_id = pdf_artifact.artifact_id
        return record

    def _get(self, url: str, allowed_origin: tuple[str, str]) -> httpx.Response:
        if _origin(url) != allowed_origin:
            raise SourceScopeViolation(f"out-of-scope URL: {url}")
        for attempt in range(self.max_attempts):
            try:
                response = self.client.get(url, follow_redirects=True)
            except httpx.HTTPError:
                if attempt + 1 == self.max_attempts:
                    raise
                sleep(2**attempt)
                continue
            if _origin(str(response.url)) != allowed_origin:
                raise SourceScopeViolation(f"out-of-scope redirect: {response.url}")
            if response.status_code in {401, 403} or b"captcha" in response.content.lower():
                raise SourceAccessBlocked(f"source access blocked: {response.status_code} {url}")
            if response.status_code in {408, 429, 500, 502, 503, 504} and attempt + 1 < self.max_attempts:
                sleep(2**attempt)
                continue
            response.raise_for_status()
            return response
        raise RuntimeError("unreachable")

    @staticmethod
    def _published_pdf_urls(html: str, page_url: str) -> list[str]:
        soup = BeautifulSoup(html, "lxml")
        urls: list[str] = []
        for link in soup.select("a.original-file[href], a[href$='.pdf']"):
            url = urljoin(page_url, link["href"])
            if url not in urls:
                urls.append(url)
        return urls


def _origin(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise SourceScopeViolation(f"invalid source URL: {url}")
    return parsed.scheme, parsed.netloc.lower()