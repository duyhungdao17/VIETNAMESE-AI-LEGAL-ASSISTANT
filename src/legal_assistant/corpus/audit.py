from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import date
from hashlib import sha256
from pathlib import Path
from typing import Any

from lxml import html as lxml_html
from lxml.etree import ParserError


def _text_length(html: str) -> int:
    try:
        tree = lxml_html.fromstring(html)
    except ParserError:
        return 0
    for node in tree.xpath('//script|//style'):
        node.drop_tree()
    return len(tree.text_content().strip())


def _safe_path(root: Path, relative: object) -> Path | None:
    if not isinstance(relative, str):
        return None
    target = (root / relative).resolve()
    return target if target.is_relative_to(root.resolve()) else None


def _source_date(value: object) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def audit_dataset(root: Path) -> dict[str, Any]:
    '''Validate a raw corpus without changing any source artifact.'''
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    record_log = _safe_path(root, manifest.get('record_log_path'))
    if record_log is None or not record_log.is_file():
        raise ValueError('manifest record_log_path is absent or unsafe')
    counts: Counter[str] = Counter()
    issues: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    referenced: set[Path] = set()
    checked: dict[Path, str] = {}
    html_text_cache: dict[Path, int] = {}
    statuses: Counter[str] = Counter()

    def issue(source_id: str, code: str) -> None:
        issues.append({'source_id': source_id, 'code': code})
        counts[code] += 1

    def artifact_bytes(source_id: str, artifact: dict[str, Any]) -> bytes | None:
        path = _safe_path(root, artifact.get('relative_path'))
        if path is None:
            issue(source_id, 'unsafe_path')
            return None
        referenced.add(path)
        if not path.is_file():
            issue(source_id, 'missing_artifact')
            return None
        payload = path.read_bytes()
        if path not in checked:
            checked[path] = sha256(payload).hexdigest()
        if checked[path] != artifact.get('sha256'):
            issue(source_id, 'hash_mismatch')
            return None
        valid = True
        if artifact.get('kind') in {'document_json', 'document_html'} and artifact.get('source_id') != source_id:
            issue(source_id, 'artifact_source_id_mismatch')
            valid = False
        if not artifact.get('source_url') or not artifact.get('fetched_at'):
            issue(source_id, 'missing_provenance')
            valid = False
        if artifact.get('http_status') != 200:
            issue(source_id, 'non_200_artifact')
            valid = False
        return payload if valid else None

    with record_log.open(encoding='utf-8') as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                issue(f'line:{line_number}', 'invalid_record_json')
                continue
            source_id = record.get('source_id')
            if not isinstance(source_id, str) or not source_id:
                issue(f'line:{line_number}', 'missing_source_id')
                continue
            counts['records'] += 1
            if source_id in seen_ids:
                issue(source_id, 'duplicate_record_id')
            seen_ids.add(source_id)
            statuses[str(record.get('source_status_label') or 'unknown')] += 1
            artifacts = record.get('artifacts') or []
            json_refs = [a for a in artifacts if a.get('kind') == 'document_json']
            html_refs = [a for a in artifacts if a.get('kind') == 'document_html']
            if len(json_refs) != 1:
                issue(source_id, 'json_reference_count')
            json_data = None
            for artifact in json_refs:
                payload = artifact_bytes(source_id, artifact)
                if payload is not None:
                    try:
                        json_data = json.loads(payload).get('data')
                    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
                        issue(source_id, 'invalid_document_json')
            if len(html_refs) > 1:
                issue(source_id, 'html_reference_count')
            if json_refs and html_refs and json_refs[0].get('source_url') != html_refs[0].get('source_url'):
                issue(source_id, 'artifact_source_url_mismatch')
                json_data = None
            html_valid = False
            for artifact in html_refs:
                payload = artifact_bytes(source_id, artifact)
                if payload is None:
                    continue
                try:
                    html = payload.decode('utf-8')
                except UnicodeDecodeError:
                    issue(source_id, 'invalid_html_encoding')
                    continue
                path = _safe_path(root, artifact['relative_path'])
                assert path is not None
                if path not in html_text_cache:
                    html_text_cache[path] = _text_length(html)
                if html_text_cache[path] == 0:
                    issue(source_id, 'empty_html')
                    continue
                html_valid = True
                if isinstance(json_data, dict):
                    embedded = json_data.get('documentContent')
                    embedded_html = embedded.get('content') if isinstance(embedded, dict) else None
                    if isinstance(embedded_html, str) and embedded_html.encode('utf-8') != payload:
                        issue(source_id, 'json_html_mismatch')
            if not html_refs:
                issue(source_id, 'missing_html')
            if not isinstance(json_data, dict):
                issue(source_id, 'missing_document_data')
            else:
                for field in ('id', 'title', 'docNum', 'docType', 'effStatus'):
                    if not json_data.get(field):
                        issue(source_id, 'missing_' + field)
                parsed_dates = {}
                for field in ('issueDate', 'effFrom', 'effTo'):
                    value = json_data.get(field)
                    parsed_dates[field] = _source_date(value)
                    if value and parsed_dates[field] is None:
                        issue(source_id, 'invalid_' + field)
                if parsed_dates['effFrom'] and parsed_dates['effTo'] and parsed_dates['effTo'] < parsed_dates['effFrom']:
                    issue(source_id, 'reversed_effective_dates')
            if html_valid and isinstance(json_data, dict) and len(json_refs) == 1 and len(html_refs) == 1 and not any(
                a.get('source_url') != json_refs[0].get('source_url') for a in html_refs
            ):
                counts['usable_documents'] += 1
            if record.get('errors'):
                issue(source_id, 'record_error')

    if counts['records'] != manifest.get('record_count'):
        issue('manifest', 'record_count_mismatch')
    checkpoint = manifest.get('checkpoint') or {}
    if checkpoint.get('completed_count') != counts['records']:
        issue('manifest', 'completed_count_mismatch')
    for artifact in manifest.get('discovery_artifacts') or []:
        artifact_bytes(str(artifact.get('source_id') or 'discovery'), artifact)
    observed = checkpoint.get('observed_total')
    seen = checkpoint.get('seen_count')
    counts['observed_total'] = observed or 0
    counts['seen_count'] = seen or 0
    counts['pending_count'] = checkpoint.get('pending_count') or 0
    counts['unseen_vs_observed'] = max(0, observed - seen) if isinstance(observed, int) and isinstance(seen, int) else 0
    state_counts = None
    state_path = root / 'sync-state.sqlite3'
    if state_path.is_file():
        connection = None
        try:
            connection = sqlite3.connect(state_path.resolve().as_uri() + '?mode=ro', uri=True)
            state_counts = {
                'seen_count': connection.execute('select count(*) from documents where seen = 1').fetchone()[0],
                'completed_count': connection.execute('select count(*) from documents where completed = 1').fetchone()[0],
                'pending_count': connection.execute('select count(*) from pending').fetchone()[0],
            }
            for key, actual in state_counts.items():
                if actual != checkpoint.get(key):
                    issue('sync-state', 'state_' + key.removesuffix('_count') + '_mismatch')
        except sqlite3.DatabaseError:
            issue('sync-state', 'state_unreadable')
        finally:
            if connection is not None:
                connection.close()
    counts['orphan_artifacts'] = sum(1 for path in (root / 'artifacts').iterdir() if path.is_file() and path.resolve() not in referenced)
    for key in ('usable_documents', 'empty_html', 'missing_html', 'hash_mismatch'):
        counts.setdefault(key, 0)
    return {
        'dataset_version': manifest.get('dataset_version'),
        'schema_version': manifest.get('schema_version'),
        'config_version': manifest.get('config_version'),
        'discovery_complete': manifest.get('discovery_complete', False),
        'discovery_incomplete': manifest.get('discovery_incomplete', False),
        'counts': dict(counts),
        'statuses': dict(statuses),
        'state_counts': state_counts,
        'issues': issues,
    }
