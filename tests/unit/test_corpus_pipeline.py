import hashlib
import sqlite3
import json
import pytest
from pathlib import Path

from legal_assistant.corpus.audit import audit_dataset
from legal_assistant.corpus.chunking import chunk_document, validate_chunk_coverage
from legal_assistant.corpus.normalize import normalize_document
from legal_assistant.corpus.pilot import _candidates, build_pilot
from legal_assistant.corpus.evaluation import GoldCase, GoldEvidence, _exact_citation, _ndcg_from_ranks, evaluate_variants, export_annotation_candidates
from legal_assistant.cli import main


def _fixture(tmp_path: Path, html: str, extra_data: dict | None = None) -> Path:
    root = tmp_path / 'raw'
    (root / 'artifacts').mkdir(parents=True)
    payload = {'data': {'id': 42, 'title': 'sample', 'documentContent': {'content': html}}}
    payload['data'].update(extra_data or {})
    refs = []
    json_bytes = json.dumps(payload).encode()
    for kind, suffix, content in (('document_json', '.json', json_bytes), ('document_html', '.html', html.encode())):
        digest = hashlib.sha256(content).hexdigest()
        relative = 'artifacts/' + digest + suffix
        (root / relative).write_bytes(content)
        refs.append({'artifact_id': 'sha256:' + digest, 'kind': kind, 'source_id': 'vbpl:42', 'source_url': 'https://example.org/42', 'fetched_at': '2026-01-01T00:00:00Z', 'sha256': digest, 'relative_path': relative, 'media_type': 'text/html' if suffix == '.html' else 'application/json', 'http_status': 200})
    (root / 'records.jsonl').write_text(json.dumps({'source_id': 'vbpl:42', 'canonical_url': 'https://example.org/42', 'artifacts': refs}) + '\n')
    (root / 'manifest.json').write_text(json.dumps({'dataset_version': 'fixture', 'schema_version': '3', 'config_version': '3', 'source_identifier': 'vbpl_gateway', 'source_base_url': 'https://example.org', 'seed_sha256': 'fixture', 'record_count': 1, 'record_log_path': 'records.jsonl', 'checkpoint': {'observed_total': 1, 'seen_count': 1, 'completed_count': 1, 'pending_count': 0}, 'discovery_complete': True}))
    return root


def test_audit_rejects_empty_html_and_corrupted_hash(tmp_path: Path) -> None:
    root = _fixture(tmp_path, '<html><body></body></html>')
    report = audit_dataset(root)
    assert report['counts']['empty_html'] == 1
    assert report['counts']['usable_documents'] == 0
    next((root / 'artifacts').glob('*.html')).write_text('corrupt')
    report = audit_dataset(root)
    assert report['counts']['hash_mismatch'] == 1


def test_audit_checks_discovery_artifacts(tmp_path: Path) -> None:
    root = _fixture(tmp_path, '<p>valid text</p>')
    discovery = b'original discovery payload'
    digest = hashlib.sha256(discovery).hexdigest()
    relative = 'artifacts/' + digest + '.json'
    (root / relative).write_bytes(b'changed')
    manifest = json.loads((root / 'manifest.json').read_text())
    manifest['discovery_artifacts'] = [{'relative_path': relative, 'sha256': digest, 'source_url': 'https://example.org/list', 'fetched_at': '2026-01-01T00:00:00Z', 'http_status': 200}]
    (root / 'manifest.json').write_text(json.dumps(manifest))
    report = audit_dataset(root)
    assert report['counts']['hash_mismatch'] == 1


def test_audit_ignores_script_and_style_text(tmp_path: Path) -> None:
    root = _fixture(tmp_path, '<html><script>fake law</script><style>fake law</style></html>')
    assert audit_dataset(root)['counts']['empty_html'] == 1


def test_audit_flags_reversed_effective_dates(tmp_path: Path) -> None:
    root = _fixture(tmp_path, '<p>valid text</p>', {'effFrom': '2026-02-01', 'effTo': '2026-01-01'})
    assert audit_dataset(root)['counts']['reversed_effective_dates'] == 1


def test_audit_reconciles_sqlite_resume_state(tmp_path: Path) -> None:
    root = _fixture(tmp_path, '<p>valid text</p>')
    with sqlite3.connect(root / 'sync-state.sqlite3') as connection:
        connection.execute('create table documents (source_id text, seen integer, completed integer)')
        connection.execute('create table pending (source_id text)')
        connection.execute('insert into documents values (?, 1, 1)', ('vbpl:42',))
        connection.execute('insert into pending values (?)', ('vbpl:42',))
    report = audit_dataset(root)
    assert report['counts']['state_pending_mismatch'] == 1


def test_audit_checks_hash_for_each_reference_to_shared_file(tmp_path: Path) -> None:
    root = _fixture(tmp_path, '<p>valid text</p>')
    first = json.loads((root / 'records.jsonl').read_text().splitlines()[0])
    second = json.loads(json.dumps(first))
    second['source_id'] = 'vbpl:43'
    second['artifacts'][1]['sha256'] = '0' * 64
    (root / 'records.jsonl').write_text(json.dumps(first) + '\n' + json.dumps(second) + '\n')
    manifest = json.loads((root / 'manifest.json').read_text())
    manifest['record_count'] = 2
    manifest['checkpoint']['completed_count'] = 2
    (root / 'manifest.json').write_text(json.dumps(manifest))
    assert audit_dataset(root)['counts']['hash_mismatch'] == 1


def test_audit_does_not_call_bad_provenance_usable(tmp_path: Path) -> None:
    root = _fixture(tmp_path, '<p>valid text</p>')
    record = json.loads((root / 'records.jsonl').read_text())
    record['artifacts'][1]['fetched_at'] = None
    record['artifacts'][1]['source_id'] = 'vbpl:other'
    (root / 'records.jsonl').write_text(json.dumps(record) + '\n')
    report = audit_dataset(root)
    assert report['counts']['missing_provenance'] == 1
    assert report['counts']['artifact_source_id_mismatch'] == 1
    assert report['counts']['usable_documents'] == 0


def test_chunking_preserves_verified_path() -> None:
    html = '<p>Ch\u01b0\u01a1ng I</p><p>\u0110i\u1ec1u 1. Ph\u1ea1m vi</p><p>1. Quy t\u1eafc chung.</p><p>a) Vi\u1ec7c th\u1ee9 nh\u1ea5t.</p><p>b) Vi\u1ec7c th\u1ee9 hai.</p>'
    doc = normalize_document('vbpl:42', 'https://example.org/42', 'hash42', {'title': 'sample', 'documentContent': {'content': html}})
    chunks = chunk_document(doc, max_chars=1000)
    assert [c.point for c in chunks if c.point] == ['a', 'b']
    assert all(c.article == '1' and c.chapter == 'I' for c in chunks)
    assert chunks[0].source_hash == 'hash42'
    assert chunks[0].source_start < chunks[0].source_end
    point_chunk = next(c for c in chunks if c.point == 'a')
    assert 'Quy t' in point_chunk.embed_text


def test_unverified_heading_has_no_precise_citation() -> None:
    html = '<p>\u0110i\u1ec1u th\u1ee9 nh\u1ea5t. N\u1ed9i dung</p><p>1. Quy \u0111\u1ecbnh A.</p>'
    doc = normalize_document('vbpl:42', 'https://example.org/42', 'hash42', {'title': 'sample', 'documentContent': {'content': html}})
    chunks = chunk_document(doc, max_chars=1000)
    assert all(c.article is None and c.clause is None for c in chunks)
    assert doc.warnings


def test_long_clause_splits_without_losing_text() -> None:
    html = '<p>\u0110i\u1ec1u 1. Ph\u1ea1m vi</p><p>1. ' + 'Long sentence. ' * 50 + '</p>'
    doc = normalize_document('vbpl:42', 'https://example.org/42', 'hash42', {'title': 'sample', 'documentContent': {'content': html}})
    chunks = chunk_document(doc, max_chars=90)
    assert len(chunks) > 1
    clause_chunks = [c for c in chunks if c.clause == '1']
    assert len(clause_chunks) > 1
    assert all(c.article == '1' for c in clause_chunks)
    assert [c.chunk_part for c in clause_chunks] == list(range(1, len(clause_chunks) + 1))
    assert ''.join(c.content for c in clause_chunks) == ('1. ' + 'Long sentence. ' * 50).rstrip()


def test_continuation_keeps_verified_clause_path() -> None:
    html = '<p>\u0110i\u1ec1u 1. Scope</p><p>1. First sentence.</p><p>Second sentence.</p>'
    doc = normalize_document('vbpl:42', 'https://example.org/42', 'hash42', {'title': 'sample', 'documentContent': {'content': html}})
    chunks = chunk_document(doc)
    clause_chunks = [c for c in chunks if c.clause == '1']
    assert len(clause_chunks) == 1
    assert 'First sentence.\nSecond sentence.' in clause_chunks[0].content
    assert clause_chunks[0].citation_verified


def test_orphan_point_does_not_become_citation(tmp_path: Path) -> None:
    html = '<p>\u0110i\u1ec1u 1. Scope</p><p>a) Orphan point.</p><p>Continuation.</p>'
    doc = normalize_document('vbpl:42', 'https://example.org/42', 'hash42', {'title': 'sample', 'documentContent': {'content': html}})
    chunks = chunk_document(doc)
    assert all(c.point is None for c in chunks)
    assert doc.warnings


def test_chunk_coverage_detects_missing_source_span() -> None:
    html = '<p>\u0110i\u1ec1u 1. Scope</p><p>1. First rule.</p><p>Continuation.</p>'
    doc = normalize_document('vbpl:42', 'https://example.org/42', 'hash42', {'title': 'sample', 'documentContent': {'content': html}})
    chunks = chunk_document(doc)
    validate_chunk_coverage(doc, chunks)
    with pytest.raises(ValueError, match='uncovered'):
        validate_chunk_coverage(doc, chunks[:-1])


def test_chapter_title_text_is_not_lost(tmp_path: Path) -> None:
    html = '<p>Ch\u01b0\u01a1ng I</p><p>General provisions</p><p>\u0110i\u1ec1u 1. Scope</p>'
    doc = normalize_document('vbpl:42', 'https://example.org/42', 'hash42', {'title': 'sample', 'documentContent': {'content': html}})
    chunks = chunk_document(doc)
    validate_chunk_coverage(doc, chunks)
    assert any('General provisions' in c.content for c in chunks)


def test_pilot_is_reproducible_and_never_overwrites(tmp_path: Path) -> None:
    root = _fixture(tmp_path, '<p>\u0110i\u1ec1u 1. Scope</p><p>1. Rule.</p>')
    first = build_pilot(root, tmp_path / 'pilot-a', limit=1, seed='seed-1')
    second = build_pilot(root, tmp_path / 'pilot-b', limit=1, seed='seed-1')
    assert first['document_ids'] == ['vbpl:42']
    assert first['chunk_count'] > 0
    assert (tmp_path / 'pilot-a' / 'chunks.jsonl').read_bytes() == (tmp_path / 'pilot-b' / 'chunks.jsonl').read_bytes()
    try:
        build_pilot(root, tmp_path / 'pilot-a', limit=1, seed='seed-1')
    except FileExistsError:
        pass
    else:
        raise AssertionError('pilot unexpectedly overwrote an existing version')


def test_pilot_chunks_keep_raw_provenance(tmp_path: Path) -> None:
    root = _fixture(tmp_path, '<p>\u0110i\u1ec1u 1. Scope</p>')
    build_pilot(root, tmp_path / 'pilot', limit=1)
    chunk = json.loads((tmp_path / 'pilot' / 'chunks.jsonl').read_text(encoding='utf-8').splitlines()[0])
    assert chunk['source_identifier'] == 'vbpl_gateway'
    assert chunk['fetched_at'] == '2026-01-01T00:00:00Z'
    assert chunk['source_hash']


def test_pilot_candidate_pool_is_not_capped_by_status(tmp_path: Path) -> None:
    root = _fixture(tmp_path, '<p>valid text</p>')
    records = [{'source_id': f'vbpl:{number}', 'source_status_label': 'active'} for number in range(105)]
    (root / 'records.jsonl').write_text('\n'.join(json.dumps(record) for record in records) + '\n')
    assert len(_candidates(root, 'seed')) == 105


def test_evaluation_separates_evidence_recall_from_citation_accuracy(tmp_path: Path) -> None:
    root = _fixture(tmp_path, '<p>\u0110i\u1ec1u 1. Scope</p><p>1. Unique rule about bananas.</p>')
    build_pilot(root, tmp_path / 'pilot', limit=1)
    doc = json.loads((tmp_path / 'pilot' / 'documents.jsonl').read_text(encoding='utf-8').splitlines()[0])
    start = doc['source_text'].index('Unique rule')
    gold = [GoldCase(query='bananas', split='test', evidence=[GoldEvidence(document_id='vbpl:42', start=start, end=start + len('Unique rule about bananas.'), article='1', clause='1')])]
    report = evaluate_variants(tmp_path / 'pilot', gold, top_k=5)
    assert report['hierarchy']['recall_at_5'] == 1.0
    assert report['hierarchy']['exact_citation_at_5'] == 1.0
    assert report['fixed']['recall_at_5'] == 1.0
    assert report['fixed']['exact_citation_at_5'] == 0.0


def test_evaluation_rejects_unresolvable_gold_span(tmp_path: Path) -> None:
    root = _fixture(tmp_path, '<p>\u0110i\u1ec1u 1. Scope</p><p>1. Rule.</p>')
    build_pilot(root, tmp_path / 'pilot', limit=1)
    gold = [GoldCase(query='Rule', split='test', evidence=[GoldEvidence(document_id='vbpl:42', start=9999, end=10000, article='1', clause='1')])]
    with pytest.raises(ValueError, match='unresolvable'):
        evaluate_variants(tmp_path / 'pilot', gold)


def test_evaluation_rejects_unverified_gold_even_if_offset_exists(tmp_path: Path) -> None:
    root = _fixture(tmp_path, '<p>Introduction only.</p>')
    build_pilot(root, tmp_path / 'pilot', limit=1)
    gold = [GoldCase(query='Introduction', split='test', evidence=[GoldEvidence(document_id='vbpl:42', start=0, end=12)])]
    with pytest.raises(ValueError, match='unresolvable'):
        evaluate_variants(tmp_path / 'pilot', gold)


def test_exact_citation_requires_substantial_evidence_overlap() -> None:
    doc = normalize_document('vbpl:42', 'https://example.org/42', 'hash42', {'documentContent': {'content': '<p>Điều 1. Scope</p><p>1. A long rule about licensing and safety.</p>'}})
    chunk = next(chunk for chunk in chunk_document(doc, max_chars=15) if chunk.clause == '1')
    evidence = GoldEvidence(document_id='vbpl:42', start=chunk.source_end - 1, end=chunk.source_end + 10, article='1', clause='1')
    assert not _exact_citation(evidence, chunk)


def test_ndcg_penalizes_late_multiple_relevant_chunks() -> None:
    assert _ndcg_from_ranks([2, 3]) == pytest.approx(0.693426, abs=1e-5)


def test_annotation_candidates_point_to_exact_source_text(tmp_path: Path) -> None:
    root = _fixture(tmp_path, '<p>\u0110i\u1ec1u 1. Scope</p><p>1. Rule text.</p>')
    build_pilot(root, tmp_path / 'pilot', limit=1)
    destination = tmp_path / 'candidates.jsonl'
    count = export_annotation_candidates(tmp_path / 'pilot', destination)
    assert count > 0
    doc = json.loads((tmp_path / 'pilot' / 'documents.jsonl').read_text(encoding='utf-8').splitlines()[0])
    candidates = [json.loads(line) for line in destination.read_text(encoding='utf-8').splitlines()]
    assert all(doc['source_text'][c['start']:c['end']] == c['text'] for c in candidates)
    assert all('query' not in c for c in candidates)


def test_cli_audit_writes_report_without_changing_raw_data(tmp_path: Path) -> None:
    root = _fixture(tmp_path, '<p>valid text</p>')
    before = (root / 'manifest.json').read_bytes()
    destination = tmp_path / 'audit.json'
    assert main(['audit-corpus', '--dataset-root', str(root), '--report-out', str(destination)]) == 0
    assert json.loads(destination.read_text(encoding='utf-8'))['counts']['records'] == 1
    assert (root / 'manifest.json').read_bytes() == before
