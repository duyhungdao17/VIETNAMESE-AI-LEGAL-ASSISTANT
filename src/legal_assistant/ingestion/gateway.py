from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from math import ceil
from time import sleep
from unicodedata import normalize
from urllib.parse import urlparse

import httpx

from .crawler import SourceAccessBlocked, SourceScopeViolation, _origin, is_access_blocked
from .gateway_state import GatewaySyncState
from .models import ArtifactKind, CrawlRecord, DatasetManifest, SeedRecord
from .storage import RawStore


@dataclass(frozen=True)
class GatewayProgress:
    pass_number: int
    max_discovery_passes: int
    page_number: int
    total_pages: int
    seen_count: int
    completed_count: int
    pending_count: int
    discovery_complete: bool
    discovery_incomplete: bool

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
        state: GatewaySyncState | None = None,
        status_scope: str = "selected",
        progress_reporter: Callable[[GatewayProgress], None] | None = None,
    ) -> None:
        if status_scope not in {"selected", "all"}:
            raise ValueError("status_scope must be selected or all")
        self.client = client
        self.store = store
        self.api_base_url = api_base_url.rstrip("/")
        self.request_delay_seconds = request_delay_seconds
        self.max_attempts = max_attempts
        self.sleep_fn = sleep_fn
        self.checkpoint_writer = checkpoint_writer
        self.state = state
        self.status_scope = status_scope
        self.progress_reporter = progress_reporter
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
        max_discovery_passes: int = 1,
    ) -> None:
        if page_size <= 0:
            raise ValueError("page_size must be positive")
        if max_pages is not None and max_pages <= 0:
            raise ValueError("max_pages must be positive or None")
        if max_discovery_passes <= 0:
            raise ValueError("max_discovery_passes must be positive")

        self._retry_pending(manifest)
        self._write_checkpoint(manifest)
        for pass_number in range(1, max_discovery_passes + 1):
            completed_full_pass, total, totals_stable = self._run_discovery_pass(
                manifest,
                pass_number=pass_number,
                page_size=page_size,
                max_pages=max_pages,
                max_discovery_passes=max_discovery_passes,
            )
            if not completed_full_pass:
                return
            seen_count = self._seen_count(manifest)
            if totals_stable and total == manifest.checkpoint.observed_total:
                manifest.checkpoint.stable_total_passes += 1
            else:
                manifest.checkpoint.stable_total_passes = 0
            required_stable_passes = 1 if max_discovery_passes == 1 else 2
            manifest.discovery_complete = (
                totals_stable
                and total is not None
                and seen_count == total
                and manifest.checkpoint.stable_total_passes >= required_stable_passes
            )
            manifest.discovery_incomplete = False
            self._write_checkpoint(manifest)
            self._report_progress(
                manifest,
                pass_number,
                max_discovery_passes,
                manifest.checkpoint.last_completed_page,
                ceil(total / page_size) if total else 0,
            )
            if manifest.discovery_complete:
                return

        manifest.discovery_incomplete = True
        self._write_checkpoint(manifest)

    def _run_discovery_pass(
        self,
        manifest: DatasetManifest,
        *,
        pass_number: int,
        page_size: int,
        max_pages: int | None,
        max_discovery_passes: int,
    ) -> tuple[bool, int | None, bool]:
        page_number = 1
        pages_processed = 0
        pass_total: int | None = None
        totals_stable = True
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
                source_id=f"{self.source_identifier}:list:pass:{pass_number}:page:{page_number}",
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
            if pass_total is None:
                pass_total = total
            elif pass_total != total:
                totals_stable = False
                manifest.retrieval_errors.append(f"pass {pass_number} total changed: {pass_total}->{total}")
                pass_total = total
            self._record_observed_total(manifest, total)
            self._process_items(manifest, items, fetched_at)
            manifest.checkpoint.last_completed_page = page_number
            pages_processed += 1
            self._write_checkpoint(manifest)
            self._report_progress(manifest, pass_number, max_discovery_passes, manifest.checkpoint.last_completed_page, ceil(total / page_size) if total else 0)

            total_pages = ceil(total / page_size)
            self._report_progress(manifest, pass_number, max_discovery_passes, page_number, total_pages)
            if page_number >= total_pages or not items:
                return True, pass_total, totals_stable
            page_number += 1
        return False, pass_total, totals_stable

    def _report_progress(self, manifest: DatasetManifest, pass_number: int, max_discovery_passes: int, page_number: int, total_pages: int) -> None:
        if self.progress_reporter is None:
            return
        if self.state is not None:
            seen_count, completed_count, pending_count = self.state.counts()
        else:
            seen_count = len(set(manifest.checkpoint.seen_source_ids))
            completed_count = len(set(manifest.checkpoint.completed_source_ids))
            pending_count = len(manifest.pending_seeds)
        self.progress_reporter(GatewayProgress(pass_number, max_discovery_passes, page_number, total_pages, seen_count, completed_count, pending_count, manifest.discovery_complete, manifest.discovery_incomplete))
    def _record_observed_total(self, manifest: DatasetManifest, total: int) -> None:
        previous = manifest.checkpoint.observed_total
        if previous is not None and previous != total:
            manifest.retrieval_errors.append(f"observed total changed: {previous}->{total}")
        manifest.checkpoint.observed_total = total

    def _process_items(self, manifest: DatasetManifest, items: list[object], discovered_at: datetime) -> None:
        seen = set(manifest.checkpoint.seen_source_ids)
        completed = set(manifest.checkpoint.completed_source_ids)
        pending = {seed.source_id: seed for seed in manifest.pending_seeds}
        for item in items:
            if not isinstance(item, dict) or item.get("id") is None:
                continue
            document_id = str(item["id"])
            source_id = f"{self.source_identifier}:{document_id}"
            already_seen = self.state.has_seen(source_id) if self.state is not None else source_id in seen
            if self.state is not None and self.state.is_completed(source_id):
                continue
            if self.state is None and source_id in completed:
                continue
            if not already_seen:
                if self.state is not None:
                    self.state.mark_seen(source_id)
                else:
                    seen.add(source_id)
                    manifest.checkpoint.seen_source_ids.append(source_id)
            status_label = _status_name(item.get("effStatus"))
            if not already_seen:
                _increment(manifest.status_counts, status_label or "unknown")
            if self.status_scope == "selected" and not _is_selected_status(status_label):
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
                selection_reason="gateway_status_filter" if self.status_scope == "selected" else "gateway_full_status_scope",
            )
            if self.state is not None and self.state.is_completed(source_id):
                continue
            if self.state is None and source_id in completed:
                continue
            try:
                record = self._fetch_detail(seed)
                self._store_record(manifest, record)
                completed.add(source_id)
            except (SourceAccessBlocked, SourceScopeViolation):
                raise
            except Exception as error:
                manifest.checkpoint.failed_source_ids.append(source_id)
                manifest.retrieval_errors.append(f"{source_id}:{type(error).__name__}:{error}")
                pending[source_id] = seed
                if self.state is not None:
                    self.state.mark_pending(source_id)
        manifest.pending_seeds = list(pending.values())

    def _retry_pending(self, manifest: DatasetManifest) -> None:
        remaining: list[SeedRecord] = []
        completed = set(manifest.checkpoint.completed_source_ids)
        for seed in manifest.pending_seeds:
            if self.state is not None and self.state.is_completed(seed.source_id):
                continue
            if self.state is None and seed.source_id in completed:
                continue
            try:
                record = self._fetch_detail(seed)
                self._store_record(manifest, record)
                completed.add(seed.source_id)
            except (SourceAccessBlocked, SourceScopeViolation):
                raise
            except Exception as error:
                manifest.retrieval_errors.append(f"{seed.source_id}:retry:{type(error).__name__}:{error}")
                remaining.append(seed)
        manifest.pending_seeds = remaining

    def _store_record(self, manifest: DatasetManifest, record: CrawlRecord) -> None:
        if self.state is None:
            manifest.records.append(record)
            manifest.checkpoint.completed_source_ids.append(record.source_id)
            return
        self.state.append_record(record)
        self.state.mark_completed(record.source_id)
        manifest.record_count += 1

    def _seen_count(self, manifest: DatasetManifest) -> int:
        if self.state is not None:
            return self.state.counts()[0]
        return len(set(manifest.checkpoint.seen_source_ids))

    def _write_checkpoint(self, manifest: DatasetManifest) -> None:
        if self.state is not None:
            seen, completed, pending = self.state.counts()
            manifest.checkpoint.seen_count = seen
            manifest.checkpoint.completed_count = completed
            manifest.checkpoint.pending_count = pending
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
        record = CrawlRecord(source_id=seed.source_id, canonical_url=seed.canonical_url, artifacts=[json_artifact], source_status_label=seed.source_status_label, selection_bucket=seed.selection_bucket)
        data = response.json().get("data", {})
        document_content = data.get("documentContent", {}) if isinstance(data, dict) else {}
        html = document_content.get("content") if isinstance(document_content, dict) else None
        if isinstance(html, str) and html.strip():
            html_artifact, _ = self.store.persist_bytes(kind=ArtifactKind.DOCUMENT_HTML, source_id=seed.source_id, source_url=str(response.url), fetched_at=fetched_at, payload=html.encode("utf-8"), media_type="text/html; charset=utf-8", http_status=response.status_code, suffix=".html", source_artifact_id=json_artifact.artifact_id)
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