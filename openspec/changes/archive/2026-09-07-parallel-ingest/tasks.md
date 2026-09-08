## 1. Batched graph writes

- [x] 1.1 Implement the type-grouped UNWIND batch write methods for entities and relations (one MERGE per type, at most one sources write-back per call) on the Neo4j client; verify `tests/engine/test_neo4j_batch.py` proves batched writes equal per-item upserts and that round trips scale with types, not items (`fb5e1923`).

## 2. Concurrency knobs and parallel pipeline

- [x] 2.1 Add `engine.ingest.chunk_concurrency` / `engine.ingest.doc_concurrency` to the app config schema, `config/app.yaml` (defaults 4/2), and the engine config plumbing; verify `tests/engine/test_config.py` covers defaults, override, and 1-equals-serial (`8251641b`).
- [x] 2.2 Parallelize the pipeline: worker-thread extraction and chunking, one TaskGroup per document running overview ∥ per-chunk analysis ∥ embedding, positional result slots for deterministic ordering, completed/total progress, and ExceptionGroup unwrapping; verify `tests/engine/test_pipeline.py` covers ordering, bounds, and failure handling (`cd7fdd9c`).
- [x] 2.3 Share the parallel analysis core across `process_file` and `reindex_document` under the document semaphore; verify pipeline tests cover both paths producing identical results (`0c6a5916`).

## 3. Batch ingest surfaces

- [x] 3.1 Add `ingest_batch` to the `KnowledgeBase` contract and implement it in the GraphRAG backend with per-file failure isolation; verify contract tests cover ordered per-file results and one-bad-file isolation (`fe874c2c`).
- [x] 3.2 Add the `POST /api/documents/upload/batch` BFF endpoint with per-file validation and per-file result entries; verify `tests/frontend/test_bff_documents.py` covers mixed batches and the empty-batch 422 (`8e422387`).
- [x] 3.3 Add the CLI `ingest-batch` subcommand with missing-file fail-fast, 0.5 s polling to terminal status, and `--timeout` (default 600 s); verify CLI tests cover completion, failure, and timeout paths (`4814e7d8`).
- [x] 3.4 Switch the SPA multi-file upload to one batch request with per-file outcomes; verify `client/src/api/__tests__/client.test.ts` covers the batch call (`638c2237`).

## 4. Documentation and verification

- [x] 4.1 Document the ingest knobs and their semantics in `src/engine/CLAUDE.md`; verify the documented keys match `config/schema.py` (`dc77ba17`).
- [x] 4.2 Run the live integration verification over the retrieval benchmark corpus (see the `retrieval-benchmark` change): batch-vs-single graph equivalence, `ingest_batch` round trip (13 s for the corpus), concurrent document pairs observable under `doc_concurrency: 2`, full `uv run pytest` suite green (307 passed, 7 skipped).
- [x] 4.3 Validate the change with `openspec validate parallel-ingest --strict` and confirm the landed commits contain only this change's files.
