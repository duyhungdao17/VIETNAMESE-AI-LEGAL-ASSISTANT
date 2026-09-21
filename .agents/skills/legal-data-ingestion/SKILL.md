---
name: legal-data-ingestion
description: Use when crawling, synchronizing, re-ingesting, or validating Vietnamese legal-source data and its raw-data lineage.
---

# Legal Data Ingestion

Preserve a reproducible, source-attributable raw corpus. A successful crawl is not authorization to replace an existing dataset or live index.

## Before external work

Identify the authoritative source, requested scope, target environment, and terms or access constraints. Ask for explicit authorization before using credentials supplied in chat, accessing production, or performing destructive re-ingestion.

## Required outputs

For every ingestion version, produce a manifest containing:

- dataset/index version and schema/config version;
- source identifier and URL, crawl time, response/content hash;
- checkpoint/resume state, retrieval errors, and counts;
- deduplication decision and original-record references.

Store raw responses immutably. Normalize or index only derived copies. Preserve documents regardless of present legal status.

## Replacement rule

Build a new raw-data and index version. Validate schema, provenance, hashes, record counts, and failures against the previous manifest. Report the difference and wait for approval before changing an alias, default pointer, or production consumer.

## Stop conditions

Stop and report when the source is not authoritative or permitted, source identity cannot be recorded, the crawl cannot resume safely, validation fails, or replacement approval is absent.

## Common mistakes

- Treating a successful crawl as permission to overwrite data.
- Recording only normalized text and losing the raw response or URL.
- Deleting expired legislation instead of modelling its status and dates.
