# Vietnamese Legal Assistant — Agent Instructions

## Purpose and safety

- This repository builds a Vietnamese legal **research** assistant. It is not legal advice.
- Do not present generated content as definitive legal advice. Preserve uncertainty and abstain when authoritative evidence is insufficient.
- Do not fabricate, repair, or silently remap legal citations.

## Source and data integrity

- Prefer authoritative Vietnamese legal sources. Preserve each record's source URL, crawl time, content hash, and source identifier.
- Keep historical and expired documents. Model effective dates, status, amendments, replacements, and version lineage; never delete them merely because they are not current.
- Preserve legal hierarchy from document through article, clause, and point. Do not use token-only chunking where it loses a legal reference.
- Treat raw source data as immutable. Normalized data and indexes must be reproducible from versioned inputs and configuration.

## Retrieval, answers, and evaluation

- Retrieval changes require a reproducible comparison with the existing baseline; report dataset, split, index version, embedding version, metrics, and latency.
- Keep evaluation-only datasets out of training and prompt-tuning data.
- Legal answers must cite only retrieved, authoritative evidence and expose source identity plus available article/clause information.
- Citation verification must check source existence, referenced hierarchy, and evidence support. If any required citation cannot be verified, return a clearly marked insufficient-evidence response rather than the unsupported claim.

## Engineering practice

- Use typed Python and Pydantic schemas for public data and API boundaries when the application is introduced.
- Keep provider-specific code behind interfaces; do not bind retrieval behavior to a single LLM or embedding vendor.
- Never commit secrets, raw credentials, or production URLs. Keep `.env` ignored and document variable names only in `.env.example`.
- Ask for explicit authorization before destructive re-ingestion, schema migration, production access, or use of credentials supplied in chat.
