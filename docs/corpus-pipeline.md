# Corpus audit, legal chunking, and evaluation

This workflow is for legal research. Raw responses are immutable; all reports and chunks are derived versions. JSON preserves source metadata and response provenance. HTML preserves the document content used to reconstruct legal hierarchy. A file's presence alone does not prove that it contains usable law text.

Keeping both files is appropriate here: JSON is the structured source response and metadata record; HTML is the exact document body used by the parser. The HTML currently duplicates content embedded in JSON, which costs storage but permits independent hash, readability, and parser checks. Do not drop either raw artifact merely to save space; any deduplicated storage format would need a separate, reproducible derived version.

## 1. Audit the full corpus

~~~powershell
.\.venv\Scripts\python.exe -m legal_assistant.cli audit-corpus --dataset-root data/raw/vbpl-gateway-full-20260926 --report-out data/processed/audit-vbpl-gateway-full-20260926.json
~~~

The command reads the record log, manifest, and every referenced document and discovery artifact. It checks paths, existence, SHA-256, JSON/UTF-8 validity, HTML text, JSON-to-HTML agreement, provenance, duplicate IDs, manifest counts, and orphan files. The report contains counts, source-status distribution, and per-ID issue codes. It does not fetch or rewrite source data. A new report path is required for each run.

Read discovery completeness separately from content usability. The observed gateway total can change during a crawl, so a stable complete listing pass and resolved pending details are needed before claiming snapshot completeness. Records with missing or empty content remain in raw storage but cannot enter citation-capable chunks.

The third full read-only audit of vbpl-gateway-full-20260926 found 37,357 completed records and 35,892 usable JSON/HTML pairs. It found 1,404 empty HTML documents, 61 missing HTML documents, 65 missing effective-status values, 34 records with an effective-end date earlier than the effective-start date, two missing document numbers, and one missing title. One reversed-date example was checked against the source JSON and is a source-value anomaly, not a parser correction. Referenced artifact hash mismatches, orphan artifacts, missing provenance, non-200 artifacts, and source ID/URL mismatches were all zero; SQLite seen/completed/pending counts matched the manifest. The crawl checkpoint reported 37,470 observed, 37,457 seen, 100 pending details, and 13 not seen relative to the observed total. Therefore this is not a complete, fully usable snapshot: the pending details and listing gap need a separate, authorized crawl continuation. The raw files are retained unchanged. See the versioned report in data/processed/audit-vbpl-gateway-full-20260926-v3.json for individual source IDs and issue codes.

## 2. Build a stratified pilot

~~~powershell
.\.venv\Scripts\python.exe -m legal_assistant.cli build-pilot --dataset-root data/raw/vbpl-gateway-full-20260926 --output data/processed/vbpl-pilot-v5 --limit 200 --seed pilot-v1 --max-chars 1800
~~~

The selection is deterministic and balances source status, document type, and HTML length across all eligible records, not an early per-status cap. The builder validates JSON/HTML hashes and their content agreement before stratifying, then revalidates selected artifacts. It writes normalized documents, citation-capable chunks, discovery-only chunks, and a pilot manifest recording input manifest hash, parser and selection versions, selection, warnings, and counts. It refuses to overwrite an existing pilot version. Pilot v5 has 200 documents, 39,501 citation-capable chunks from 173 documents, 27 discovery-only documents, 1,982 discovery-only chunks, and 583 parser warnings; 514 annotation candidates were exported. Pilot v4 remains an exploratory historical version made with the capped candidate pool.

The parser reads document, part, chapter, section, article, clause, and point boundaries. It keeps a verified ancestor path with each chunk. The smallest supported unit is used where possible; unusually long units split into ordered parts inside their parent. Fixed-size windows and whole-article chunks are comparison baselines only. Unverified paths are marked and kept out of the citation-capable chunk file. Tables, annexes, malformed headings, and dates still require manual review; neither the parser nor a retrieval score verifies legal applicability.

The primary retrieval boundary is the smallest verified legal unit, normally a point or clause and otherwise an article. Each unit carries its document identity, version/provenance, ancestor headings, source-text offsets, and available effective dates. A long unit may become ordered subchunks, but never crosses its legal parent. Short continuation paragraphs are joined to their preceding unit when the verified path is unchanged. The embed text adds a short document/hierarchy context; the cited content and offsets remain the source excerpt.

Whole-resolution or whole-chapter chunks are too broad for precise evidence retrieval and can mix provisions with different conditions. Pure semantic segmentation may group related language, but its boundaries can cross an article or clause and make exact legal references ambiguous. Semantic similarity is better tested later for candidate retrieval or reranking over these citation-safe units, not as the only source of truth for chunk boundaries. If a heading cannot be verified, retain the text in discovery-only output, surface a parser warning, and do not manufacture an article/clause/point citation.

## 3. Prepare human gold labels and compare

~~~powershell
.\.venv\Scripts\python.exe -m legal_assistant.cli prepare-gold-candidates --pilot-dir data/processed/vbpl-pilot-v5 --output data/eval/runs/vbpl-pilot-v5-candidates.jsonl
~~~

Candidates contain source ID, URL/hash, exact offsets, path, and text. They are annotation aids, not gold labels. Have a reviewer write approximately 120 answerable questions plus 20 insufficient-evidence questions spanning article lookup, clause/point detail, definitions, time-sensitive cases, and cross-references. Each gold JSONL line uses the GoldCase schema: query, split (dev or test), answerable, and evidence entries with document_id, start, end, article, clause, and point. Verify each question against the source document. Keep document families and versions in only one split; freeze test labels before choosing settings.

~~~powershell
.\.venv\Scripts\python.exe -m legal_assistant.cli evaluate-chunking --pilot-dir data/processed/vbpl-pilot-v5 --gold-file data/eval/gold-v1.jsonl --split test --top-k 20 --report-out data/eval/runs/chunking-bm25-test-v1.json
~~~

The evaluator requires each labeled span to resolve inside a verified article path before scoring. It compares fixed windows, article chunks, and hierarchical chunks using the same local BM25 implementation and corpus. Evidence coverage and exact-citation credit require at least 80% of the gold span; citation credit additionally requires a verified matching legal path. The report includes Recall@5/10/20, exact citation rate, MRR, NDCG, per-query ranks, p50/p95 query latency, chunk counts, pilot manifest hash, and gold hash. Missing human labels are a missing evaluation result, not a zero score. Dense embeddings and answer-generation/abstention metrics need separate frozen configurations and labels before those claims can be made.

An eight-question provisional dev smoke set is included at data/eval/dev-smoke-v1.jsonl. Its spans and legal paths were checked against the generated candidates, but the questions were drafted during implementation, not independently reviewed. It is not a sealed test set or a representative legal benchmark. Run it with:

~~~powershell
.\.venv\Scripts\python.exe -m legal_assistant.cli evaluate-chunking --pilot-dir data/processed/vbpl-pilot-v5 --gold-file data/eval/dev-smoke-v1.jsonl --split dev --top-k 20 --report-out data/eval/runs/chunking-bm25-dev-smoke-pilot-v5.json
~~~

On pilot v5 and this smoke set, fixed/article/hierarchy Recall@10 was 0.75/0.875/0.75, MRR was 0.466/0.629/0.576, and exact citation@10 was 0/0/0.75. Their p95 local BM25 query latencies were approximately 29/84/227 ms. These are diagnostic numbers only. The article baseline retrieves broader spans more easily; the hierarchical variant preserves clause and point identity but creates more chunks and has higher lexical-search latency. The hierarchy variant missed both questions about source ID vbpl:176350 in its top 10, so document-level aggregation or lexical normalization is worth testing next. Use an independently reviewed, document-disjoint test set to choose a production method.

Run the local tests with:

~~~powershell
.\.venv\Scripts\python.exe -m pytest -q
~~~
