---
name: legal-rag-evaluation
description: Use when designing, running, comparing, or reporting Vietnamese legal retrieval, RAG, citation, or abstention evaluations.
---

# Legal RAG Evaluation

Measure a frozen system on provenance-tracked data. A score is not credible when test labels, prompts, or settings influenced the system being measured.

## Split discipline

Keep training, development, and held-out test roles separate. Treat evaluation-only datasets and official test labels as sealed: do not use them for model training, embedding/reranker tuning, prompt examples, threshold selection, or error-driven iteration. If no development split exists, create and document a deterministic non-test split that avoids document/version leakage.

## Run contract

Record dataset revision/hash and split, corpus/index manifest, temporal policy, chunking, embedding/sparse/reranker/generator identities, prompts, dependency lockfile, judge-model settings, date, sample count, failures, and per-query outputs. Freeze configuration before a final test run; any tuning after observing it makes that run development evidence.

## Metrics and report

Report retrieval `Recall@k`, `MRR`, `NDCG`, Hit Rate, latency, and error rate before generation metrics. Use RAGAS where suitable for faithfulness, answer relevancy, context precision, and context recall, while also reporting deterministic legal metrics: citation validity/precision/recall, hierarchy correctness, evidence support, and abstention accuracy.

Compare each variant with the same baseline and split. Publish aggregate deltas, variance where generation/judging is nondeterministic, per-query artifacts, costs, and qualitative failure cases. Include a time-shifted or out-of-domain check when feasible.

## Common mistakes

- Tuning until an official test result improves.
- Calling LLM-judge scores proof without recording judge configuration.
- Hiding abstentions, failures, or citation-audit regressions behind an average.