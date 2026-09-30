from __future__ import annotations

import re
from datetime import date, datetime

from bs4 import BeautifulSoup
from pydantic import BaseModel, Field


class LegalUnit(BaseModel):
    kind: str
    label: str | None = None
    text: str
    start: int
    end: int
    part: str | None = None
    chapter: str | None = None
    section: str | None = None
    article: str | None = None
    clause: str | None = None
    point: str | None = None
    citation_verified: bool = False


class NormalizedDocument(BaseModel):
    document_id: str
    source_identifier: str | None = None
    source_url: str
    source_hash: str
    fetched_at: datetime | None = None
    source_status_label: str | None = None
    title: str | None = None
    document_number: str | None = None
    document_type: str | None = None
    effective_status: str | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    source_text: str
    units: list[LegalUnit] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


_HEADINGS = (
    ('part', re.compile(r'^(?:PH\u1ea6N|Ph\u1ea7n)\s+([IVXLCDM]+|\d+)\b')),
    ('chapter', re.compile(r'^(?:CH\u01af\u01a0NG|Ch\u01b0\u01a1ng)\s+([IVXLCDM]+|\d+)\b')),
    ('section', re.compile(r'^(?:M\u1ee4C|M\u1ee5c)\s+([IVXLCDM]+|\d+)\b')),
    ('article', re.compile(r'^(?:\u0110I\u1ec0U|\u0110i\u1ec1u)\s+(\d+[a-zA-Z]?)\s*[.:]?(?:\s|$)')),
    ('clause', re.compile(r'^(\d+)\.\s+')),
    ('point', re.compile(r'^([a-z\u0111])\)\s+', re.IGNORECASE)),
)


def _source_lines(html: str) -> list[str]:
    soup = BeautifulSoup(html, 'lxml')
    for tag in soup(['script', 'style']):
        tag.decompose()
    blocks = soup.select('p, li, h1, h2, h3, h4, tr')
    if not blocks:
        return [line.strip() for line in soup.get_text('\n').splitlines() if line.strip()]
    lines = []
    for tag in blocks:
        if tag.name == 'tr' and tag.find(['p', 'li']):
            continue
        if tag.name not in {'tr', 'li'} and tag.find_parent(['p', 'li']):
            continue
        value = ' '.join(tag.stripped_strings)
        if value and (not lines or value != lines[-1]):
            lines.append(value)
    return lines


def _date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def normalize_document(document_id: str, source_url: str, source_hash: str, data: dict[str, object], *, source_identifier: str | None = None, fetched_at: datetime | str | None = None, source_status_label: str | None = None) -> NormalizedDocument:
    content = data.get('documentContent')
    html = content.get('content') if isinstance(content, dict) else None
    lines = _source_lines(html) if isinstance(html, str) else []
    source_text = '\n'.join(lines)
    doc_type = data.get('docType')
    status = data.get('effStatus')
    result = NormalizedDocument(
        document_id=document_id,
        source_identifier=source_identifier,
        source_url=source_url,
        source_hash=source_hash,
        fetched_at=fetched_at,
        source_status_label=source_status_label,
        title=data.get('title') if isinstance(data.get('title'), str) else None,
        document_number=data.get('docNum') if isinstance(data.get('docNum'), str) else None,
        document_type=doc_type.get('name') if isinstance(doc_type, dict) else None,
        effective_status=status.get('name') if isinstance(status, dict) else None,
        effective_from=_date(data.get('effFrom')),
        effective_to=_date(data.get('effTo')),
        source_text=source_text,
    )
    path: dict[str, str | None] = {key: None for key in ('part', 'chapter', 'section', 'article', 'clause', 'point')}
    position = 0
    for line in lines:
        start = position
        position += len(line) + 1
        match = next(((kind, regex.match(line)) for kind, regex in _HEADINGS if regex.match(line)), None)
        if match:
            kind, found = match
            assert found is not None
            path[kind] = found.group(1)
            keys = list(path)
            for lower in keys[keys.index(kind) + 1:]:
                path[lower] = None
            verified = kind in {'article', 'clause', 'point'} and path['article'] is not None
            if kind == 'point' and path['clause'] is None:
                verified = False
                result.warnings.append(f'orphan point at offset {start}')
                path['article'] = path['point'] = None
        else:
            kind, verified = 'text', path['article'] is not None
            if re.match(r'^(?:\u0110i\u1ec1u|\u0110I\u1ec0U)\b', line):
                result.warnings.append(f'unverified article heading at offset {start}')
                path['article'] = path['clause'] = path['point'] = None
                verified = False
            elif re.match(r'^\d+\.\s+', line) and path['article'] is None:
                result.warnings.append(f'orphan clause at offset {start}')
        result.units.append(LegalUnit(kind=kind, label=path.get(kind), text=line, start=start, end=start + len(line), citation_verified=verified, **path))
    return result
