from datetime import UTC, datetime

from legal_assistant.ingestion.models import CrawlRecord


def test_gateway_state_persists_seen_completed_and_pending_ids(tmp_path) -> None:
    from legal_assistant.ingestion.gateway_state import GatewaySyncState

    state = GatewaySyncState(tmp_path)
    state.mark_seen("vbpl:1")
    state.mark_completed("vbpl:1")
    state.mark_pending("vbpl:2")
    state.close()

    resumed = GatewaySyncState(tmp_path)
    assert resumed.has_seen("vbpl:1") is True
    assert resumed.is_completed("vbpl:1") is True
    assert resumed.pending_ids() == ["vbpl:2"]
    resumed.close()


def test_gateway_state_appends_records_without_rewriting_manifest(tmp_path) -> None:
    from legal_assistant.ingestion.gateway_state import GatewaySyncState

    state = GatewaySyncState(tmp_path)
    state.append_record(CrawlRecord(source_id="vbpl:1", canonical_url="https://example.test/1"))
    state.close()

    lines = (tmp_path / "records.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert '"source_id":"vbpl:1"' in lines[0]

def test_gateway_state_recovers_completed_record_from_append_log(tmp_path) -> None:
    from legal_assistant.ingestion.gateway_state import GatewaySyncState

    state = GatewaySyncState(tmp_path)
    state.append_record(CrawlRecord(source_id="vbpl:9", canonical_url="https://example.test/9"))
    state.close()

    resumed = GatewaySyncState(tmp_path)
    assert resumed.is_completed("vbpl:9") is True
    resumed.close()