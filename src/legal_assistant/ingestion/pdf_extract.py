from __future__ import annotations

import json
from datetime import UTC, datetime

import pypdf
from pypdf import PdfReader

from .models import ArtifactKind, PdfExtractionRecord, RawArtifact
from .storage import RawStore


class PdfTextExtractor:
    def __init__(self, store: RawStore) -> None:
        self.store = store

    def extract(self, raw_pdf: RawArtifact) -> PdfExtractionRecord:
        try:
            reader = PdfReader(self.store.dataset_root / raw_pdf.relative_path)
            pages = [page.extract_text() or "" for page in reader.pages]
        except Exception as error:
            return PdfExtractionRecord(source_artifact_id=raw_pdf.artifact_id, status="quarantined", extractor_version=pypdf.__version__, warning=f"pdf_read_error:{type(error).__name__}")
        if not any(text.strip() for text in pages):
            return PdfExtractionRecord(source_artifact_id=raw_pdf.artifact_id, status="quarantined", extractor_version=pypdf.__version__, page_count=len(pages), warning="no_extractable_text")
        payload = json.dumps({"source_artifact_id": raw_pdf.artifact_id, "pages": pages}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        text_artifact, _ = self.store.persist_bytes(kind=ArtifactKind.PDF_TEXT, source_id=raw_pdf.source_id, source_url=raw_pdf.source_url, fetched_at=datetime.now(UTC), payload=payload, media_type="application/json", http_status=raw_pdf.http_status, suffix=".pdf-text.json", source_artifact_id=raw_pdf.artifact_id)
        return PdfExtractionRecord(source_artifact_id=raw_pdf.artifact_id, status="extracted", extractor_version=pypdf.__version__, page_count=len(pages), text_artifact=text_artifact)