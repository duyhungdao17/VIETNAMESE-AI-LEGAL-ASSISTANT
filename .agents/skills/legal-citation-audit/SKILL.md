---
name: legal-citation-audit
description: Use when generating, validating, repairing, or reviewing citations and evidence support in Vietnamese legal RAG answers.
---

# Legal Citation Audit

A citation is valid only when retrieved authoritative evidence supports the specific claim at the referenced legal hierarchy. Topic similarity is not support.

## Audit each claim-citation pair

1. Parse the cited source and hierarchy without inventing missing components.
2. Confirm the source exists in retrieved context and matches its source ID/URL.
3. Confirm the document, article, clause, and point (when cited) exist in the normalized hierarchy.
4. Confirm the cited passage supports the claim’s legal subject, condition, modality, date, and exception where applicable.
5. Record evidence chunk IDs, offsets or quotations, status, temporal applicability, and failure reason.

## Outcome policy

Return a structured pass/fail result per citation and claim. A missing source, hierarchy mismatch, unsupported claim, uncertain hierarchy, or unavailable authoritative evidence fails verification. Remove or downgrade unsupported claims; when required citations cannot be verified, return a clearly marked insufficient-evidence answer rather than an apparently confident legal conclusion.

Do not repair a citation by guessing a nearby clause, using an unretrieved source, or changing a claim’s meaning. Preserve the original model output and audit trail for review.

## Common mistakes

- Verifying that an article exists but not its cited clause.
- Treating semantic similarity as evidence for dates, amounts, actors, or exceptions.
- Hiding failed citations instead of returning an abstention or limitation.