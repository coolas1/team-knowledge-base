## Context

The pre-change pipeline (`src/engine/graphrag/pipeline.py`) was sequential: blocking file extraction on the event loop, then chunk-by-chunk LLM analysis, then a single embedding batch, then per-entity and per-relation `MERGE` round trips to Neo4j (one network operation per graph item). The only upload surfaces were one-file-per-request (`POST /api/documents/upload`, CLI `ingest`) and an HTTP client loop. See proposal.md - Why for the motivation.

## Goals / Non-Goals

**Goals:**
- Cut ingest wall clock by overlapping the I/O-bound stages (LLM analysis, embedding, graph writes) without changing what is stored.
- Keep writes deterministic — parallelism must not reorder chunks, misalign embeddings, or change the resulting graph.
- Give every surface (contract, CLI, BFF, SPA) a batch path where one bad file never sinks the batch.

**Non-Goals:**
- No cross-process parallelism or external job queue — all bounds are in-process asyncio.
- No changes to retrieval, chunking strategy, or the analysis prompts.
- No resume/checkpoint for interrupted batches; a failed file is simply reported and can be retried.

## Decisions

### One task group per document, two semaphores
`asyncio.TaskGroup` runs overview analysis, the embedding batch, and one analysis task per chunk concurrently. Two `asyncio.Semaphore`s bound the fan-out: chunk-level (default 4) and document-level (default 2), both constructed as `max(1, n)` so 0/negative values degrade to serial rather than deadlock.
- Why: structured concurrency gives cancellation-on-failure for free; semaphores are the cheapest bound that maps 1:1 onto the config knobs.
- Alternative: a fixed worker pool — rejected: extra machinery to bound the same thing, and pool sizing would duplicate the knobs.

### Determinism by positional slots
Chunk analyses are written into a pre-sized `results[index]` slot; progress is a completed/total counter; embeddings are requested in chunk order and consumed positionally. Persist happens only after the whole group succeeds, so a cancelled group never writes partial state.
- Why: this is what makes "parallel MUST equal serial" a testable invariant rather than a hope.
- Alternative: collecting futures and sorting by completion — rejected: reordering risk at persist time.

### Exception-group unwrapping for readable failures
TaskGroup wraps a single child failure in an `ExceptionGroup`; a small helper unwraps the one-exception case so the document's `error_msg` stays human-readable.
- Why: operators read `error_msg` in the UI; `ExceptionGroup` noise would obscure the real cause.

### UNWIND batching grouped by type
Entities/relations are flattened from all chunk analyses, grouped by type, and written with one `UNWIND $rows … MERGE` per type plus at most one sources write-back per call. Per-item upsert methods remain for single-item operations.
- Why: round trips drop from O(items) to O(distinct types); `MERGE` preserves the natural dedup semantics.
- Verified by `tests/engine/test_neo4j_batch.py`, which asserts batch writes produce the same graph as per-item upserts (also confirmed live on 2026-09-03).

### One shared analysis core for both ingest paths
`process_file` and `reindex_document` call the same `_analyze_document`, with extraction (`asyncio.to_thread`) and `chunk_text` moved off the event loop; the document semaphore wraps the whole extract+analyze stage.
- Why: two paths that must stay behaviorally identical should share the code that produces the difference; reindex gets parallelism for free.

### Batch semantics: enqueue-all, isolate failures, return refs immediately
`ingest_batch` loops the existing per-file ingest (which persists the document row and spawns the background pipeline task), catching per-file exceptions into a failed `DocumentRef` instead of aborting the loop. Pipeline tasks keep running after the call returns.
- Why: matches the existing single-ingest async model; callers that need terminal status poll `get_document` (exactly what the CLI does).
- Alternative: `asyncio.gather(return_exceptions=True)` over full-pipeline coroutines — rejected: would change ingest timing semantics and duplicate the background-task wiring.

### BFF per-file validation before ingest
The batch endpoint reuses the single-upload validation per file and short-circuits invalid files into `{"ok": false, "error": …}` entries; FastAPI's request validation supplies the empty-batch 422.
- Why: identical error payloads to the single endpoint, so the SPA renders one error component for both.

### CLI polls; SPA sends one request
`ingest-batch` polls every 0.5 s until terminal state or `--timeout` (default 600 s). The SPA client gained a single batch call with per-file results.
- Why: polling is scriptable and needs no new streaming surface; the SPA batch call replaces its client-side sequential loop.

## Risks / Trade-offs

- [TaskGroup cancels sibling tasks on first failure] → intended: a failed chunk means the document fails; nothing is persisted until the group succeeds.
- [Higher concurrent load on the LLM/embedding endpoints] → the chunk knob is the dial for that; defaults (4/2) are conservative and were validated live.
- [Progress granularity becomes completed/total rather than position] → progress strings already render counts; no consumer depends on positional progress.
- [UNWIND with very large per-type batches] → a document's chunk count bounds batch size in practice; the 48-file corpus of the 2026-09-03 live run never approached a problematic size.

## Migration Plan

Already applied on `main` (`fb5e1923`..`dc77ba17`). Knobs are additive with defaults that preserve prior serial semantics only when set to 1 — defaults enable bounded parallelism (4/2), which was accepted as the new normal after live verification. New endpoints/subcommands are additive; no existing request shapes changed. Rollback = revert the commit range; no schema or data changes.

## Open Questions

None — the live 2026-09-03 run (13 s batch round trip, batch-equals-single graph equivalence, 307-test suite green) answered the correctness questions this design left open.
