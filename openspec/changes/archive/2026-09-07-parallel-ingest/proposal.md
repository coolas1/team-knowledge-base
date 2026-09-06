## Why

Ingestion was serial end to end: chunks were analyzed and embedded one at a time, and every entity/relation was written to Neo4j with its own round trip, so indexing a multi-document corpus took minutes of wall clock dominated by idle waits. The web UI could only upload one file per request, and one bad file in a manual multi-file session could sink the batch. Parallelizing ingest with explicit bounds — and adding a failure-isolated batch path across the contract, CLI, and BFF — turns corpus indexing into a bounded-latency operation without changing what gets stored.

## What Changes

- Parallelize the ingest pipeline: text extraction moves to a worker thread, and document overview, per-chunk analysis, and embedding run concurrently in one task group per document.
- Bound parallelism with two independent knobs in `config/app.yaml`: `engine.ingest.chunk_concurrency` (per-document chunk analysis/embedding, default 4) and `engine.ingest.doc_concurrency` (documents in the analysis stage, default 2); 1 restores serial behavior.
- Share one parallel analysis core across both ingest paths (new-file processing and edit/reindex), so reindexing gets the same parallelism and produces identical results.
- Batch Neo4j graph writes: entities and relations are grouped by type and written with one set-based `UNWIND` operation per group plus at most one source-annotation write-back, replacing per-item MERGE round trips. Batched and per-item writes MUST produce an identical graph.
- Add an `ingest_batch` method to the `KnowledgeBase` contract with per-file failure isolation: one failing file yields a failed result for that file while the rest continue.
- Add a CLI `ingest-batch` command that enqueues files and polls each document to a terminal status (`indexed`/`failed`) or a timeout.
- Add a BFF batch upload endpoint (`POST /api/documents/upload/batch`) that validates and isolates failures per file and returns a per-file result list.
- Switch the web client's multi-file upload to the batch endpoint (one request, per-file outcomes).

## Capabilities

### New Capabilities
- `ingest`: Document ingestion behavior — bounded parallel processing with deterministic results, batched knowledge-graph writes, batch ingest across the contract/CLI/BFF surfaces with per-file failure isolation, and multi-file upload from the web client.

### Modified Capabilities

None.

## Impact

- `src/engine/graphrag/pipeline.py` — rewritten around the shared parallel analysis core (task group, two semaphores, worker-thread extraction, deterministic ordering).
- `src/engine/components/store/neo4j.py` — new batch write methods (`UNWIND` per type, sources write-back); per-item upserts retained for single operations.
- `src/engine/interface.py`, `src/engine/graphrag/backend.py`, `src/engine/cli.py` — `ingest_batch` contract method, backend implementation, `ingest-batch` CLI subcommand with status polling.
- `config/app.yaml`, `config/schema.py`, `src/engine/config.py` — `engine.ingest.*` knobs and plumbing.
- `src/frontend/webapp/server/routes_documents.py`, `client/src/api/client.ts`, `client/src/components/Layout.tsx` — batch endpoint, client call, multi-file upload UI.
- Tests: `tests/engine/test_pipeline.py`, `test_neo4j_batch.py`, `test_config.py`, `test_cli.py`, `test_contract.py`, `tests/frontend/test_bff_documents.py`, SPA client tests.
- Retroactive note: this change documents work already applied to `main` (commits `fb5e1923`..`dc77ba17`). The artifacts record what landed; they do not gate it. Its live verification ran on the retrieval benchmark corpus (see the separate `retrieval-benchmark` change).
