from __future__ import annotations

from hashlib import sha256
from datetime import datetime

from pydantic import BaseModel, Field

from .normalize import LegalUnit, NormalizedDocument


class LegalChunk(BaseModel):
    chunk_id: str
    document_id: str
    source_identifier: str | None = None
    source_url: str
    source_hash: str
    fetched_at: datetime | None = None
    source_status_label: str | None = None
    source_start: int
    source_end: int
    content: str
    embed_text: str
    heading: str | None = None
    part: str | None = None
    chapter: str | None = None
    section: str | None = None
    article: str | None = None
    clause: str | None = None
    point: str | None = None
    chunk_part: int = 1
    citation_verified: bool = False
    effective_status: str | None = None
    effective_from: str | None = None
    effective_to: str | None = None
    warnings: list[str] = Field(default_factory=list)


def _pieces(text: str, limit: int) -> list[tuple[int, int]]:
    if limit < 1:
        raise ValueError('max_chars must be positive')
    return [(i, min(i + limit, len(text))) for i in range(0, len(text), limit)]


def _unit_chunks(doc: NormalizedDocument, unit: LegalUnit, max_chars: int, context: str) -> list[LegalChunk]:
    path = {key: getattr(unit, key) for key in ('part', 'chapter', 'section', 'article', 'clause', 'point')}
    verified = unit.article is not None and unit.citation_verified
    if not verified:
        path['article'] = path['clause'] = path['point'] = None
    prefix = ' | '.join(str(value) for value in (doc.document_number, doc.title) if value)
    hierarchy = ' / '.join(f'{key}:{value}' for key, value in path.items() if value)
    heading = unit.text if unit.kind in {'article', 'clause', 'point'} else None
    parts = []
    for sequence, (start, end) in enumerate(_pieces(unit.text, max_chars), 1):
        content = unit.text[start:end]
        source_start, source_end = unit.start + start, unit.start + end
        key = '|'.join(str(value) for value in (doc.document_id, doc.source_hash, source_start, source_end, hierarchy))
        parts.append(LegalChunk(
            chunk_id=sha256(key.encode('utf-8')).hexdigest(),
            document_id=doc.document_id,
            source_identifier=doc.source_identifier,
            source_url=doc.source_url,
            source_hash=doc.source_hash,
            fetched_at=doc.fetched_at,
            source_status_label=doc.source_status_label,
            source_start=source_start,
            source_end=source_end,
            content=content,
            embed_text=' | '.join(value for value in (prefix, hierarchy, context, content) if value),
            heading=heading,
            chunk_part=sequence,
            citation_verified=verified,
            effective_status=doc.effective_status,
            effective_from=doc.effective_from.isoformat() if doc.effective_from else None,
            effective_to=doc.effective_to.isoformat() if doc.effective_to else None,
            warnings=[] if verified else ['unverified_citation_path'],
            **path,
        ))
    return parts


def chunk_document(doc: NormalizedDocument, *, max_chars: int = 1800) -> list[LegalChunk]:
    if max_chars < 1:
        raise ValueError('max_chars must be positive')
    chunks = []
    headings: dict[str, str] = {}
    def emit(unit: LegalUnit) -> None:
        if unit.kind in {'part', 'chapter', 'section'}:
            headings.clear()
            return
        if unit.kind == 'article':
            headings.clear()
        elif unit.kind == 'clause':
            headings.pop('clause', None)
            headings.pop('point', None)
        elif unit.kind == 'point':
            headings.pop('point', None)
        context = ' | '.join(headings[key][:300] for key in ('article', 'clause', 'point') if key in headings)
        chunks.extend(_unit_chunks(doc, unit, max_chars, context))
        if unit.citation_verified and unit.kind in {'article', 'clause', 'point'}:
            headings[unit.kind] = unit.text.split('\n', 1)[0]

    pending: LegalUnit | None = None
    for unit in doc.units:
        if pending is not None and pending.kind not in {'part', 'chapter', 'section'} and unit.kind == 'text' and pending.citation_verified == unit.citation_verified and all(
            getattr(pending, key) == getattr(unit, key) for key in ('part', 'chapter', 'section', 'article', 'clause', 'point')
        ) and unit.start == pending.end + 1:
            pending = pending.model_copy(update={'text': doc.source_text[pending.start:unit.end], 'end': unit.end})
            continue
        if pending is not None:
            emit(pending)
        pending = unit
    if pending is not None:
        emit(pending)
    return chunks


def validate_chunk_coverage(doc: NormalizedDocument, chunks: list[LegalChunk]) -> None:
    spans = sorted(chunks, key=lambda chunk: (chunk.source_start, chunk.source_end))
    ids: set[str] = set()
    previous_end = 0
    for chunk in spans:
        if chunk.chunk_id in ids:
            raise ValueError('duplicate chunk ID')
        ids.add(chunk.chunk_id)
        if chunk.document_id != doc.document_id or chunk.source_hash != doc.source_hash:
            raise ValueError('chunk provenance mismatch')
        if chunk.source_start < previous_end:
            raise ValueError('overlapping chunk spans')
        if chunk.source_end > len(doc.source_text) or doc.source_text[chunk.source_start:chunk.source_end] != chunk.content:
            raise ValueError('chunk source offset mismatch')
        previous_end = chunk.source_end
    position = 0
    for unit in doc.units:
        if unit.kind in {'part', 'chapter', 'section'}:
            continue
        while position < len(spans) and spans[position].source_end <= unit.start:
            position += 1
        cursor = unit.start
        next_position = position
        while cursor < unit.end:
            if next_position >= len(spans) or spans[next_position].source_start > cursor:
                raise ValueError(f'uncovered source span at {unit.start}')
            cursor = min(unit.end, spans[next_position].source_end)
            if cursor < unit.end:
                next_position += 1
