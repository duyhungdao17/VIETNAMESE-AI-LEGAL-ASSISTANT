from datetime import UTC, datetime

from pypdf import PdfWriter

from legal_assistant.ingestion.models import ArtifactKind
from legal_assistant.ingestion.pdf_extract import PdfTextExtractor
from legal_assistant.ingestion.storage import RawStore


def test_blank_pdf_is_quarantined_without_text_artifact(tmp_path) -> None:
    pdf_path = tmp_path / "scan.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    with pdf_path.open("wb") as handle:
        writer.write(handle)

    store = RawStore(tmp_path / "raw")
    raw_pdf, _ = store.persist_bytes(
        kind=ArtifactKind.ORIGINAL_PDF,
        source_id="vbpl:scan",
        source_url="https://example.test/scan.pdf",
        fetched_at=datetime(2026, 9, 22, tzinfo=UTC),
        payload=pdf_path.read_bytes(),
        media_type="application/pdf",
        http_status=200,
        suffix=".pdf",
    )

    result = PdfTextExtractor(store).extract(raw_pdf)

    assert result.status == "quarantined"
    assert result.text_artifact is None
    assert result.warning == "no_extractable_text"