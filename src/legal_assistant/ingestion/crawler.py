from __future__ import annotations

from collections.abc import Callable
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


def is_access_blocked(response: httpx.Response) -> bool:
    if response.status_code in {401, 403}:
        return True
    if response.status_code != 200:
        return False
    content = response.content.lower()
    if b"captcha" not in content:
        return False
    soup = BeautifulSoup(response.text, "lxml")
    title = soup.title.get_text(" ", strip=True).lower() if soup.title else ""
    body_text = soup.get_text(" ", strip=True).lower()
    challenge_markers = (
        "verify you are human",
        "complete the captcha",
        "access denied",
        "unusual traffic",
    )
    has_challenge_form = bool(soup.select("form [name='g-recaptcha-response'], form [name='h-captcha-response']"))
    return has_challenge_form or "captcha" in title or any(marker in body_text for marker in challenge_markers)

class CrawlService:
    def __init__(
        self,
        *,
        client: httpx.Client,
        store: RawStore,
        source_identifier: str,
        max_attempts: int = 3,
        request_delay_seconds: float = 0,
        sleep_fn: Callable[[float], None] = sleep,
    ) -> None:
        self.client = client
        self.store = store
        self.source_identifier = source_identifier
        self.max_attempts = max_attempts
        self.request_delay_seconds = request_delay_seconds
        self.sleep_fn = sleep_fn

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

    def crawl_api(self, seed: SeedRecord) -> CrawlRecord:
        if seed.api_detail_url is None:
            return self.crawl(seed)
        api_origin = _origin(seed.api_detail_url)
        response = self._get(seed.api_detail_url, api_origin)
        fetched_at = datetime.now(UTC)
        json_artifact, _ = self.store.persist_bytes(
            kind=ArtifactKind.DOCUMENT_JSON,
            source_id=seed.source_id,
            source_url=str(response.url),
            fetched_at=fetched_at,
            payload=response.content,
            media_type=response.headers.get("content-type", "application/json"),
            http_status=response.status_code,
            suffix=".json",
        )
        try:
            data = response.json().get("data", {})
        except ValueError:
            data = {}
        document_content = data.get("documentContent", {}) if isinstance(data, dict) else {}
        html = document_content.get("content") if isinstance(document_content, dict) else None
        if not isinstance(html, str) or not html.strip():
            record = self.crawl(seed)
            record.artifacts.insert(0, json_artifact)
            return record
        html_artifact, _ = self.store.persist_bytes(
            kind=ArtifactKind.DOCUMENT_HTML,
            source_id=seed.source_id,
            source_url=str(response.url),
            fetched_at=fetched_at,
            payload=html.encode("utf-8"),
            media_type="text/html; charset=utf-8",
            http_status=response.status_code,
            suffix=".html",
            source_artifact_id=json_artifact.artifact_id,
        )
        record = CrawlRecord(
            source_id=seed.source_id,
            canonical_url=seed.canonical_url,
            artifacts=[json_artifact, html_artifact],
            source_status_label=seed.source_status_label,
            selection_bucket=seed.selection_bucket,
        )
        document_origin = _origin(seed.canonical_url)
        for pdf_url in self._published_pdf_urls(html, seed.canonical_url):
            pdf_response = self._get(pdf_url, document_origin)
            pdf_artifact, deduplicated = self.store.persist_bytes(
                kind=ArtifactKind.ORIGINAL_PDF,
                source_id=seed.source_id,
                source_url=str(pdf_response.url),
                fetched_at=datetime.now(UTC),
                payload=pdf_response.content,
                media_type=pdf_response.headers.get("content-type", "application/pdf"),
                http_status=pdf_response.status_code,
                suffix=".pdf",
            )
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
                self.sleep_fn(2**attempt)
                continue
            if self.request_delay_seconds:
                self.sleep_fn(self.request_delay_seconds)
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
            if _origin(str(response.url)) != allowed_origin:
                raise SourceScopeViolation(f"out-of-scope redirect: {response.url}")
            if is_access_blocked(response):
                raise SourceAccessBlocked(f"source access blocked: {response.status_code} {url}")
            if response.status_code in {408, 429, 500, 502, 503, 504} and attempt + 1 < self.max_attempts:
                self.sleep_fn(2**attempt)
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