from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from hashlib import sha256

from pydantic import BaseModel, Field, model_validator


class SourceMode(StrEnum):
    API = "api"
    HTML = "html"
    HYBRID = "hybrid"


class SourceProfile(BaseModel):
    source_identifier: str
    mode: SourceMode = SourceMode.HYBRID
    listing_url: str | None = None
    api_list_url: str | None = None
    api_detail_url_template: str | None = None
    document_url_template: str | None = None
    request_delay_seconds: float = Field(default=1.5, ge=0)

    @model_validator(mode="after")
    def validate_mode_endpoints(self) -> "SourceProfile":
        if self.mode in {SourceMode.API, SourceMode.HYBRID} and not all((self.api_list_url, self.api_detail_url_template, self.document_url_template)):
            raise ValueError("API and hybrid modes require API list, API detail, and document URL templates")
        if self.mode is SourceMode.HTML and self.listing_url is None:
            raise ValueError("HTML mode requires a listing URL")
        return self

class ArtifactKind(StrEnum):
    DOCUMENT_JSON = "document_json"
    DOCUMENT_HTML = "document_html"
    ORIGINAL_PDF = "original_pdf"
    PDF_TEXT = "pdf_text"


class SeedRecord(BaseModel):
    source_id: str
    canonical_url: str
    discovered_at: datetime
    selection_bucket: str
    source_status_label: str | None = None
    selection_reason: str | None = None
    api_detail_url: str | None = None


class RawArtifact(BaseModel):
    artifact_id: str
    kind: ArtifactKind
    source_id: str
    source_url: str
    fetched_at: datetime
    sha256: str
    relative_path: str
    media_type: str
    http_status: int
    source_artifact_id: str | None = None

    @classmethod
    def from_bytes(cls, *, artifact_id: str, kind: ArtifactKind, source_id: str, source_url: str, fetched_at: datetime, payload: bytes, relative_path: str, media_type: str, http_status: int, source_artifact_id: str | None = None) -> RawArtifact:
        return cls(artifact_id=artifact_id, kind=kind, source_id=source_id, source_url=source_url, fetched_at=fetched_at, sha256=sha256(payload).hexdigest(), relative_path=relative_path, media_type=media_type, http_status=http_status, source_artifact_id=source_artifact_id)


class PdfExtractionRecord(BaseModel):
    source_artifact_id: str
    status: str
    extractor_version: str
    page_count: int | None = None
    warning: str | None = None
    text_artifact: RawArtifact | None = None


class CrawlRecord(BaseModel):
    source_id: str
    canonical_url: str
    artifacts: list[RawArtifact] = Field(default_factory=list)
    pdf_extractions: list[PdfExtractionRecord] = Field(default_factory=list)
    source_status_label: str | None = None
    selection_bucket: str | None = None
    deduplicates_artifact_id: str | None = None
    errors: list[str] = Field(default_factory=list)


class CrawlCheckpoint(BaseModel):
    completed_source_ids: list[str] = Field(default_factory=list)
    failed_source_ids: list[str] = Field(default_factory=list)
    stopped_reason: str | None = None


class DatasetManifest(BaseModel):
    dataset_version: str
    schema_version: str
    config_version: str
    source_identifier: str
    source_base_url: str
    seed_sha256: str
    records: list[CrawlRecord] = Field(default_factory=list)
    checkpoint: CrawlCheckpoint = Field(default_factory=CrawlCheckpoint)
    retrieval_errors: list[str] = Field(default_factory=list)