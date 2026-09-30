from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .chunking import LegalChunk
from .normalize import NormalizedDocument


class GoldEvidence(BaseModel):
    document_id: str
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    article: str | None = None
    clause: str | None = None
    point: str | None = None

    @model_validator(mode='after')
    def valid_span(self) -> 'GoldEvidence':
        if self.end <= self.start:
            raise ValueError('evidence end must follow start')
        return self


class GoldCase(BaseModel):
    query: str = Field(min_length=1)
    split: Literal['dev', 'test']
    evidence: list[GoldEvidence] = Field(default_factory=list)
    answerable: bool = True

    @model_validator(mode='after')
    def has_evidence(self) -> 'GoldCase':
        if self.answerable and not self.evidence:
            raise ValueError('answerable case requires verified evidence')
        return self


class AnnotationCandidate(BaseModel):
    document_id: str
    title: str | None
    source_url: str
    source_hash: str
    start: int
    end: int
    article: str | None
    clause: str | None
    point: str | None
    text: str


def export_annotation_candidates(pilot: Path, output: Path, *, per_document: int = 3) -> int:
    if per_document < 1:
        raise ValueError('per_document must be positive')
    output.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with output.open('x', encoding='utf-8', newline='\n') as handle:
        for line in (pilot / 'documents.jsonl').open(encoding='utf-8'):
            if not line.strip():
                continue
            doc = NormalizedDocument.model_validate_json(line)
            eligible = [unit for unit in doc.units if unit.citation_verified and unit.kind in {'point', 'clause', 'article'}]
            eligible.sort(key=lambda unit: ({'point': 0, 'clause': 1, 'article': 2}[unit.kind], unit.start))
            for unit in eligible[:per_document]:
                candidate = AnnotationCandidate(
                    document_id=doc.document_id,
                    title=doc.title,
                    source_url=doc.source_url,
                    source_hash=doc.source_hash,
                    start=unit.start,
                    end=unit.end,
                    article=unit.article,
                    clause=unit.clause,
                    point=unit.point,
                    text=doc.source_text[unit.start:unit.end],
                )
                handle.write(candidate.model_dump_json() + '\n')
                count += 1
    return count


def _tokens(text: str) -> list[str]:
    return re.findall(r'\w+', text.casefold(), flags=re.UNICODE)


class BM25Index:
    def __init__(self, chunks: list[LegalChunk]) -> None:
        self.chunks = chunks
        self.lengths = []
        self.postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
        for index, chunk in enumerate(chunks):
            frequencies = Counter(_tokens(chunk.embed_text))
            self.lengths.append(sum(frequencies.values()))
            for term, frequency in frequencies.items():
                self.postings[term].append((index, frequency))
        self.average_length = sum(self.lengths) / max(1, len(self.lengths))

    def search(self, query: str, limit: int) -> list[LegalChunk]:
        scores: dict[int, float] = defaultdict(float)
        for term in set(_tokens(query)):
            posting = self.postings.get(term, [])
            if not posting:
                continue
            idf = math.log(1 + (len(self.chunks) - len(posting) + 0.5) / (len(posting) + 0.5))
            for index, frequency in posting:
                length = self.lengths[index]
                scores[index] += idf * frequency * 2.2 / (frequency + 1.2 * (0.25 + 0.75 * length / max(1, self.average_length)))
        ranked = sorted(scores, key=lambda index: (-scores[index], self.chunks[index].chunk_id))[:limit]
        return [self.chunks[index] for index in ranked]


def _baseline_chunk(doc: NormalizedDocument, start: int, end: int, article: str | None, variant: str) -> LegalChunk:
    content = doc.source_text[start:end]
    key = f'{variant}|{doc.document_id}|{doc.source_hash}|{start}|{end}'
    return LegalChunk(
        chunk_id=sha256(key.encode()).hexdigest(),
        document_id=doc.document_id,
        source_identifier=doc.source_identifier,
        source_url=doc.source_url,
        source_hash=doc.source_hash,
        fetched_at=doc.fetched_at,
        source_status_label=doc.source_status_label,
        source_start=start,
        source_end=end,
        content=content,
        embed_text=' | '.join(value for value in (doc.document_number, doc.title, f'article {article}' if article else None, content) if value),
        article=article,
        citation_verified=article is not None,
        effective_status=doc.effective_status,
    )


def _baseline_chunks(doc: NormalizedDocument, variant: str, max_chars: int) -> list[LegalChunk]:
    if variant == 'fixed':
        return [_baseline_chunk(doc, start, min(start + max_chars, len(doc.source_text)), None, variant) for start in range(0, len(doc.source_text), max_chars)]
    spans: dict[str, list[int]] = {}
    for unit in doc.units:
        if unit.article:
            span = spans.setdefault(unit.article, [unit.start, unit.end])
            span[1] = max(span[1], unit.end)
    chunks = []
    for article, (start, end) in spans.items():
        for offset in range(start, end, max_chars):
            chunks.append(_baseline_chunk(doc, offset, min(offset + max_chars, end), article, variant))
    return chunks


def _covered(evidence: GoldEvidence, chunks: list[LegalChunk]) -> bool:
    intervals = sorted((max(evidence.start, chunk.source_start), min(evidence.end, chunk.source_end)) for chunk in chunks if chunk.document_id == evidence.document_id and chunk.source_start < evidence.end and chunk.source_end > evidence.start)
    covered = 0
    last = evidence.start
    for start, end in intervals:
        covered += max(0, end - max(start, last))
        last = max(last, end)
    return covered >= 0.8 * (evidence.end - evidence.start)


def _exact_citation(evidence: GoldEvidence, chunk: LegalChunk) -> bool:
    return chunk.document_id == evidence.document_id and chunk.citation_verified and _covered(evidence, [chunk]) and all(getattr(chunk, key) == getattr(evidence, key) for key in ('article', 'clause', 'point') if getattr(evidence, key) is not None)


def _ndcg_from_ranks(relevant_ranks: list[int]) -> float:
    if not relevant_ranks:
        return 0.0
    dcg = sum(1 / math.log2(rank + 1) for rank in relevant_ranks)
    ideal = sum(1 / math.log2(rank + 1) for rank in range(1, len(relevant_ranks) + 1))
    return dcg / ideal


def evaluate_variants(pilot: Path, gold: list[GoldCase], *, top_k: int = 20, split: str = 'test') -> dict:
    if top_k < 1:
        raise ValueError('top_k must be positive')
    cases = [case for case in gold if case.split == split and case.answerable]
    if not cases:
        raise ValueError('no labeled answerable cases for the requested split')
    pilot_manifest = json.loads((pilot / 'pilot_manifest.json').read_text(encoding='utf-8'))
    docs = [NormalizedDocument.model_validate_json(line) for line in (pilot / 'documents.jsonl').open(encoding='utf-8') if line.strip()]
    documents_by_id = {doc.document_id: doc for doc in docs}
    split_documents: dict[str, set[str]] = defaultdict(set)
    for case in gold:
        for evidence in case.evidence:
            doc = documents_by_id.get(evidence.document_id)
            if doc is None or evidence.article is None or evidence.end > len(doc.source_text) or not any(
                unit.citation_verified and unit.start <= evidence.start and evidence.end <= unit.end
                and all(getattr(unit, key) == getattr(evidence, key) for key in ('article', 'clause', 'point') if getattr(evidence, key) is not None)
                for unit in doc.units
            ):
                raise ValueError(f'unresolvable gold evidence: {evidence.document_id}:{evidence.start}-{evidence.end}')
            split_documents[case.split].add(evidence.document_id)
    if split_documents['dev'] & split_documents['test']:
        raise ValueError('document leakage between dev and test gold splits')
    hierarchical = [LegalChunk.model_validate_json(line) for line in (pilot / 'chunks.jsonl').open(encoding='utf-8') if line.strip()]
    variants = {'fixed': [], 'article': [], 'hierarchy': hierarchical}
    for doc in docs:
        for variant in ('fixed', 'article'):
            variants[variant].extend(_baseline_chunks(doc, variant, pilot_manifest['max_chars']))
    report = {}
    for name, chunks in variants.items():
        index = BM25Index(chunks)
        per_query = []
        latencies = []
        for case in cases:
            start_time = perf_counter()
            ranked = index.search(case.query, top_k)
            latencies.append((perf_counter() - start_time) * 1000)
            row = {'query': case.query, 'ranked_chunk_ids': [chunk.chunk_id for chunk in ranked]}
            for k in (5, 10, 20):
                if k > top_k:
                    continue
                candidates = ranked[:k]
                row[f'recall_at_{k}'] = sum(_covered(e, candidates) for e in case.evidence) / len(case.evidence)
                row[f'exact_citation_at_{k}'] = float(any(_exact_citation(e, c) for e in case.evidence for c in candidates))
            first = next((rank for rank in range(1, len(ranked) + 1) if any(_covered(e, ranked[:rank]) for e in case.evidence)), None)
            row['reciprocal_rank'] = 1 / first if first else 0.0
            relevant_ranks = [rank for rank, chunk in enumerate(ranked, 1) if any(chunk.document_id == e.document_id and (min(chunk.source_end, e.end) - max(chunk.source_start, e.start)) >= 0.5 * (e.end - e.start) for e in case.evidence)]
            row['ndcg'] = _ndcg_from_ranks(relevant_ranks)
            per_query.append(row)
        summary = {'chunk_count': len(chunks), 'case_count': len(cases), 'mrr': sum(row['reciprocal_rank'] for row in per_query) / len(cases), 'ndcg': sum(row['ndcg'] for row in per_query) / len(cases), 'p50_latency_ms': sorted(latencies)[len(latencies) // 2], 'p95_latency_ms': sorted(latencies)[min(len(latencies) - 1, math.ceil(0.95 * len(latencies)) - 1)], 'per_query': per_query}
        for k in (5, 10, 20):
            if k <= top_k:
                for metric in ('recall', 'exact_citation'):
                    key = f'{metric}_at_{k}'
                    summary[key] = sum(row[key] for row in per_query) / len(cases)
        report[name] = summary
    return report
