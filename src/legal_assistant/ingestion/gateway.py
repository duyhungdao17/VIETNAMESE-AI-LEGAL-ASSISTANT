from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from math import ceil
from time import sleep
from unicodedata import normalize
from urllib.parse import urlparse

import httpx

from .crawler import SourceAccessBlocked, SourceScopeViolation, _origin, is_access_blocked
from .models import ArtifactKind, CrawlRecord, DatasetManifest, SeedRecord
from .storage import RawStore


class VBPLGatewaySyncService:
    """Checkpointable sync for the public VBPL gateway contract."""

    source_identifier = "vbpl"

    def __init__(
        self,
        *,
        client: httpx.Client,
        store: RawStore,
        api_base_url: str,
        request_delay_seconds: float = 1.5,
        max_attempts: int = 3,
        sleep_fn: Callable[[float], None] = sleep,
        checkpoint_writer: Callable[[DatasetManifest], None] | None = None,
    ) -> None:
        self.client = client
        self.store = store
        self.api_base_url = api_base_url.rstrip("/")
        self.request_delay_seconds = request_delay_seconds
        self.max_attempts = max_attempts
        self.sleep_fn = sleep_fn
        self.checkpoint_writer = checkpoint_writer
        _origin(self.api_base_url)

    @property
    def list_url(self) -> str:
        return f"{self.api_base_url}/qtdc/public/doc/all"

    def detail_url(self, document_id: str) -> str:
        return f"{self.api_base_url}/qtdc/public/doc/{document_id}"

    def sync(
        self,
        manifest: DatasetManifest,
        *,
        page_size: int = 10,
        max_pages: int | None = 100,
    ) -> None:
        if page_size <= 0:
            raise ValueError("page_size must be positive")
        if max_pages is not None and max_pages <= 0:
            raise ValueError("max_pages must be positive or None")

        self._retry_pending(manifest)
        self._write_checkpoint(manifest)
        page_number = manifest.checkpoint.last_completed_page + 1
        pages_processed = 0

        while max_pages is None or pages_processed < max_pages:
            response = self._request(
                "POST",
                self.list_url,
                json={
                    "pageSize": page_size,
                    "pageNumber": page_number,
                    "sortDirection": "desc",
                    "sortBy": "viewCount",
                    "sortByViewCount": True,
                },
            )
            fetched_at = datetime.now(UTC)
            list_artifact, _ = self.store.persist_bytes(
                kind=ArtifactKind.DISCOVERY_JSON,
                source_id=f"{self.source_identifier}:list:page:{page_number}",
                source_url=str(response.url),
                fetched_at=fetched_at,
                payload=response.content,
                media_type=response.headers.get("content-type", "application/json"),
                http_status=response.status_code,
                suffix=".json",
            )
            manifest.discovery_artifacts.append(list_artifact)
            payload = response.json().get("data", {})
            items = payload.get("items", [])
            total = payload.get("total")
            if not isinstance(items, list) or not isinstance(total, int):
                raise ValueError("gateway list response requires data.total and data.items")
            self._record_observed_total(manifest, total)
            self._process_items(manifest, items, fetched_at)
            manifest.checkpoint.last_completed_page = page_number
            pages_processed += 1
            self._write_checkpoint(manifest)

            total_pages = ceil(total / page_size)
            if page_number >= total_pages or not items:
                seen_count = len(set(manifest.checkpoint.seen_source_ids))
                manifest.discovery_complete = seen_count == total
                manifest.discovery_incomplete = not manifest.discovery_complete
                self._write_checkpoint(manifest)
                return
            page_number += 1

    def _record_observed_total(self, manifest: DatasetManifest, total: int) -> None:
        previous = manifest.checkpoint.observed_total
        if previous is not None and previous != total:
            manifest.retrieval_errors.append(f"observed total changed: {previous}->{total}")
        manifest.checkpoint.observed_total = total

    def _process_items(
        self,
        manifest: DatasetManifest,
        items: list[object],
        discovered_at: datetime,
    ) -> None:
        seen = set(manifest.checkpoint.seen_source_ids)
        completed = set(manifest.checkpoint.completed_source_ids)
        pending = {seed.source_id: seed for seed in manifest.pending_seeds}
        for item in items:
            if not isinstance(item, dict) or item.get("id") is None:
                continue
            document_id = str(item["id"])
            source_id = f"{self.source_identifier}:{document_id}"
            if source_id in seen:
                continue
            seen.add(source_id)
            manifest.checkpoint.seen_source_ids.append(source_id)
            status_label = _status_name(item.get("effStatus"))
            _increment(manifest.status_counts, status_label or "unknown")
            if not _is_selected_status(status_label):
                _increment(manifest.skipped_status_counts, status_label or "unknown")
                continue

            canonical_url = item.get("document_url")
            if not isinstance(canonical_url, str) or urlparse(canonical_url).scheme != "https":
                canonical_url = self.detail_url(document_id)
            seed = SeedRecord(
                source_id=source_id,
                canonical_url=canonical_url,
                api_detail_url=self.detail_url(document_id),
                discovered_at=discovered_at,
                source_status_label=status_label,
                selection_bucket=_selection_bucket(status_label),
                selection_reason="gateway_status_filter",
            )
            if source_id in completed:
                continue
            try:
                manifest.records.append(self._fetch_detail(seed))
                manifest.checkpoint.completed_source_ids.append(source_id)
            except (SourceAccessBlocked, SourceScopeViolation):
                raise
            except Exception as error:
                manifest.checkpoint.failed_source_ids.append(source_id)
                manifest.retrieval_errors.append(f"{source_id}:{type(error).__name__}:{error}")
                pending[source_id] = seed
        manifest.pending_seeds = list(pending.values())

    def _retry_pending(self, manifest: DatasetManifest) -> None:
        remaining: list[SeedRecord] = []
        completed = set(manifest.checkpoint.completed_source_ids)
        for seed in manifest.pending_seeds:
            if seed.source_id in completed:
                continue
            try:
                manifest.records.append(self._fetch_detail(seed))
                manifest.checkpoint.completed_source_ids.append(seed.source_id)
            except (SourceAccessBlocked, SourceScopeViolation):
                raise
            except Exception as error:
                manifest.retrieval_errors.append(f"{seed.source_id}:retry:{type(error).__name__}:{error}")
                remaining.append(seed)
        manifest.pending_seeds = remaining

    def _write_checkpoint(self, manifest: DatasetManifest) -> None:
        if self.checkpoint_writer is not None:
            self.checkpoint_writer(manifest)

    def _fetch_detail(self, seed: SeedRecord) -> CrawlRecord:
        if seed.api_detail_url is None:
            raise ValueError("gateway seed requires api_detail_url")
        response = self._request("GET", seed.api_detail_url)
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
        record = CrawlRecord(
            source_id=seed.source_id,
            canonical_url=seed.canonical_url,
            artifacts=[json_artifact],
            source_status_label=seed.source_status_label,
            selection_bucket=seed.selection_bucket,
        )
        data = response.json().get("data", {})
        document_content = data.get("documentContent", {}) if isinstance(data, dict) else {}
        html = document_content.get("content") if isinstance(document_content, dict) else None
        if isinstance(html, str) and html.strip():
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
            record.artifacts.append(html_artifact)
        return record

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
                    delay = float(retry_after) if retry_after is not None else 2**attempt
                except ValueError:
                    delay = 2**attempt
                self.sleep_fn(delay)
                continue
            if response.status_code in {408, 500, 502, 503, 504} and attempt + 1 < self.max_attempts:
                self.sleep_fn(2**attempt)
                continue
            response.raise_for_status()
            return response
        raise RuntimeError("unreachable")


def _status_name(value: object) -> str | None:
    return value.get("name") if isinstance(value, dict) and isinstance(value.get("name"), str) else None


def _normalized(value: str | None) -> str:
    return normalize("NFD", value.casefold()).encode("ascii", "ignore").decode("ascii") if value else ""


def _is_selected_status(value: str | None) -> bool:
    return _normalized(value) in {"con hieu luc", "het hieu luc mot phan", "chua co hieu luc"}


def _selection_bucket(value: str | None) -> str:
    status = _normalized(value)
    if status == "con hieu luc":
        return "active"
    if status == "het hieu luc mot phan":
        return "partially_expired"
    if status == "chua co hieu luc":
        return "future"
    return "unclassified"


def _increment(counts: dict[str, int], key: str) -> None:
    counts[key] = counts.get(key, 0) + 1