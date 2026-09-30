from __future__ import annotations

import json
from collections import defaultdict, deque
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from .audit import _safe_path, _text_length
from .chunking import chunk_document, validate_chunk_coverage
from .normalize import normalize_document


PARSER_VERSION = 'hierarchy-v4'
SELECTION_VERSION = 'all-eligible-strata-v2'


def _valid_payload(root: Path, artifact: dict) -> bytes | None:
    path = _safe_path(root, artifact.get('relative_path'))
    if path is None or not path.is_file():
        return None
    payload = path.read_bytes()
    return payload if sha256(payload).hexdigest() == artifact.get('sha256') else None


def _candidates(root: Path, seed: str) -> list[dict]:
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    record_log = _safe_path(root, manifest.get('record_log_path'))
    if record_log is None or not record_log.is_file():
        raise ValueError('invalid record log')
    groups: dict[str, list[tuple[str, dict]]] = defaultdict(list)
    with record_log.open(encoding='utf-8') as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            source_id = record.get('source_id')
            if not isinstance(source_id, str):
                continue
            key = sha256((seed + '|' + source_id).encode()).hexdigest()
            status = record.get('source_status_label') or 'unknown'
            groups[status].append((key, record))
    return [record for group in groups.values() for _, record in sorted(group)]


def build_pilot(root: Path, output: Path, *, limit: int = 200, seed: str = 'pilot-v1', max_chars: int = 1800) -> dict:
    if limit < 1 or max_chars < 1:
        raise ValueError('limit and max_chars must be positive')
    if output.exists():
        raise FileExistsError(output)
    manifest_bytes = (root / 'manifest.json').read_bytes()
    source_identifier = json.loads(manifest_bytes).get('source_identifier')
    strata: dict[tuple[str, str, str], list[tuple[str, dict, dict, dict]]] = defaultdict(list)
    rejected: dict[str, str] = {}
    for record in _candidates(root, seed):
        source_id = record['source_id']
        artifacts = record.get('artifacts') or []
        json_artifact = next((a for a in artifacts if a.get('kind') == 'document_json'), None)
        html_artifact = next((a for a in artifacts if a.get('kind') == 'document_html'), None)
        if json_artifact is None or html_artifact is None:
            rejected[source_id] = 'missing_json_or_html'
            continue
        if not source_identifier or not record.get('canonical_url') or any(
            artifact.get('source_id') != source_id or not artifact.get('source_url') or not artifact.get('fetched_at') or artifact.get('http_status') != 200
            for artifact in (json_artifact, html_artifact)
        ) or json_artifact.get('source_url') != html_artifact.get('source_url'):
            rejected[source_id] = 'missing_provenance'
            continue
        json_bytes = _valid_payload(root, json_artifact)
        html_bytes = _valid_payload(root, html_artifact)
        if json_bytes is None or html_bytes is None:
            rejected[source_id] = 'missing_or_corrupt_artifact'
            continue
        try:
            data = json.loads(json_bytes)['data']
            html = html_bytes.decode('utf-8')
        except (ValueError, KeyError, UnicodeDecodeError, TypeError):
            rejected[source_id] = 'invalid_content'
            continue
        embedded = data.get('documentContent') if isinstance(data, dict) else None
        if not isinstance(embedded, dict) or embedded.get('content') != html or not _text_length(html):
            rejected[source_id] = 'empty_or_mismatched_content'
            continue
        doc_type = data.get('docType')
        type_name = doc_type.get('name') if isinstance(doc_type, dict) else 'unknown'
        length = len(html_bytes)
        bucket = 'short' if length < 10000 else 'medium' if length < 100000 else 'long'
        stratum = (str(record.get('source_status_label') or 'unknown'), str(type_name), bucket)
        rank = sha256((seed + '|' + source_id).encode()).hexdigest()
        strata[stratum].append((rank, record, json_artifact, html_artifact))
    queues = {key: deque(sorted(values)) for key, values in strata.items()}
    selected = []
    while len(selected) < limit and any(queues.values()):
        for key in sorted(queues):
            if queues[key] and len(selected) < limit:
                selected.append((key, queues[key].popleft()))
    staging = output.with_name('.' + output.name + '.' + uuid4().hex + '.tmp')
    staging.mkdir(parents=True)
    document_ids = []
    chunk_count = 0
    discovery_count = 0
    warning_count = 0
    try:
        with (staging / 'documents.jsonl').open('w', encoding='utf-8', newline='\n') as docs_file, (staging / 'chunks.jsonl').open('w', encoding='utf-8', newline='\n') as chunks_file, (staging / 'discovery_chunks.jsonl').open('w', encoding='utf-8', newline='\n') as discovery_file:
            for _, (_, record, json_artifact, html_artifact) in selected:
                json_bytes = _valid_payload(root, json_artifact)
                html_bytes = _valid_payload(root, html_artifact)
                if json_bytes is None or html_bytes is None:
                    raise ValueError(f'selected artifact changed during pilot build: {record["source_id"]}')
                data = json.loads(json_bytes)['data']
                if data['documentContent']['content'].encode('utf-8') != html_bytes:
                    raise ValueError(f'selected JSON/HTML mismatch: {record["source_id"]}')
                doc = normalize_document(record['source_id'], record['canonical_url'], html_artifact['sha256'], data, source_identifier=source_identifier, fetched_at=html_artifact['fetched_at'], source_status_label=record.get('source_status_label'))
                document_chunks = chunk_document(doc, max_chars=max_chars)
                validate_chunk_coverage(doc, document_chunks)
                docs_file.write(doc.model_dump_json() + '\n')
                document_ids.append(doc.document_id)
                warning_count += len(doc.warnings)
                for chunk in document_chunks:
                    if chunk.citation_verified:
                        chunks_file.write(chunk.model_dump_json() + '\n')
                        chunk_count += 1
                    else:
                        discovery_file.write(chunk.model_dump_json() + '\n')
                        discovery_count += 1
        result = {
            'dataset_version': json.loads(manifest_bytes).get('dataset_version'),
            'input_manifest_sha256': sha256(manifest_bytes).hexdigest(),
            'parser_version': PARSER_VERSION,
            'selection_version': SELECTION_VERSION,
            'selection_seed': seed,
            'selection_limit': limit,
            'max_chars': max_chars,
            'document_ids': document_ids,
            'document_count': len(document_ids),
            'chunk_count': chunk_count,
            'discovery_chunk_count': discovery_count,
            'parser_warning_count': warning_count,
            'rejected_candidates': rejected,
            'stratum_counts': {'|'.join(key): sum(1 for chosen_key, _ in selected if chosen_key == key) for key in strata},
        }
        (staging / 'pilot_manifest.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        staging.rename(output)
        return result
    except Exception:
        for path in staging.iterdir():
            path.unlink()
        staging.rmdir()
        raise
