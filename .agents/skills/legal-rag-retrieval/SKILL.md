---
name: legal-rag-retrieval
description: Use when changing legal-search embeddings, Qdrant collections, sparse or dense retrieval, fusion, reranking, metadata filters, or temporal query behavior.
---

# Legal RAG Retrieval

Treat retrieval changes as measurable experiments, not intuition. Preserve legal source identity and hierarchy through every candidate stage.

## Experiment contract

Freeze and record the dataset/split, corpus manifest, chunking configuration, index schema/version, embedding model and dimension, sparse configuration, Qdrant version, query settings, and runtime. Compare the existing baseline with one changed variable at a time; run reranking as a separate variant.

For hybrid search, use named dense and sparse vectors with Qdrant `prefetch` and a documented fusion method. RRF is the safe default when scores are not calibrated; tune weighted fusion only on a training/validation split. Do not combine raw cosine and BM25 scores without calibrated normalization.

## Temporal and metadata behavior

Apply `as_of_date` and status filters deliberately. Unknown or ambiguous effective dates must be represented in the result policy, not silently treated as expired. Every candidate must retain source ID/URL, document ID, hierarchy, and effective-status metadata.

## Required report

Publish a reproducible comparison: variants, dataset/split, index and embedding versions, Recall@5/10/20, MRR, NDCG, Hit Rate, p50/p95 latency, error count, and qualitative failures for exact references, amendments, historical queries, and unanswerable queries. Do not claim an improvement without this evidence.

## Common mistakes

- Changing corpus, embedding, and fusion together.
- Reporting a test-set result used to tune parameters.
- Dropping source metadata before context construction.