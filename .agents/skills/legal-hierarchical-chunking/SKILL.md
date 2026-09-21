---
name: legal-hierarchical-chunking
description: Use when parsing Vietnamese legal-document structure or creating retrieval chunks that must retain document, article, clause, and point citations.
---

# Legal Hierarchical Chunking

Make legal hierarchy the primary retrieval boundary. Token limits are a fallback for one unusually long provision, never a reason to merge or invent legal references.

## Boundary policy

Parse `document → part → chapter → section → article → clause → point`. Prefer the smallest self-contained supported unit: point, otherwise clause, otherwise article. Split a single oversized unit only into ordered subparts that retain its exact parent path and an explicit `chunk_part` sequence.

Every chunk carries stable `chunk_id`, `document_id`, canonical hierarchy fields, heading, source URL, temporal metadata, source hash, and an `embed_text` that prepends enough verified hierarchy to distinguish the provision.

## Parse uncertainty

Preserve malformed headings and raw text. Attach content only to the nearest verified parent when safe, record a parser warning/review status, and do not manufacture a precise article, clause, or point. Such chunks may aid broad discovery but cannot support a precise citation until validated.

## Validate

Check stable IDs, parent-child order, no duplicate/overlapping spans, complete source offsets, citation-path rendering, and boundary coverage against the normalized document. Write a chunk manifest with parser/config version and warnings.

## Common mistakes

- Fixed-size chunks that straddle provisions.
- Storing a citation only in generated text rather than metadata.
- Losing inherited document or temporal metadata in child chunks.