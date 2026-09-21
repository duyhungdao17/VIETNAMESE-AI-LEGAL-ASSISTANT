---
name: legal-document-normalization
description: Use when extracting, normalizing, validating, or versioning metadata and hierarchy from Vietnamese legal documents.
---

# Legal Document Normalization

Create derived records that are traceable to immutable source material. Normalization clarifies supported facts; it never repairs legal citations or ambiguous dates.

## Preserve first

Keep the source identifier, URL, crawl time, content hash, raw label/text, and parser output. Attach normalized fields to the source record rather than replacing it.

## Normalize only supported values

Canonicalize document number, title, authority, hierarchy labels, and dates only when the source supplies enough evidence. Preserve legal lineage and temporal fields: `effective_from`, `effective_to`, `effective_status`, `amended_by`, `replaces`, and `superseded_by`.

For uncertainty, retain the literal source text, set an explicit review status/warning, and leave the derived field absent. A cross-reference is not a verified target until its document, article, clause, and point resolve against known source hierarchy.

## Validate before downstream use

Check required provenance, stable IDs, hashes, hierarchy order, date consistency, duplicate policy, and lineage links. Quarantine records with unresolved identifiers or hierarchy from citation-capable retrieval; do not silently discard them.

## Output contract

Emit normalized records plus a validation report containing counts by status, warnings, rejected/quarantined record identifiers, configuration version, and input manifest version.

## Common mistakes

- Guessing an effective date or clause number from context.
- Rewriting a source article label to make a citation appear valid.
- Replacing raw data with a normalized record.