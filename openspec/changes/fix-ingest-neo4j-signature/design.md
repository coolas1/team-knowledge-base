## Context

The versioned-documents change (a2819592, 17fca214) added `version_number` /
`is_current` parameters to the base `Neo4jClient.upsert_document_node` and to
all three call sites (pipeline fresh-ingest `pipeline.py:229`, pipeline
reingest `pipeline.py:387`, backend version-edit projection `backend.py:765/773`).
The source-preserving projection (`SourceNeo4jClient`, landed later in the
memory-consolidation batch) overrides the method with the pre-versioning
4-parameter signature. `build()` wires `SourceNeo4jClient` unconditionally
(`backend.py:1014`), so production always takes the override — every full
ingest raises TypeError after chunks are already persisted in Postgres.

Tests covered each class in isolation: `tests/engine/test_versioning.py:151`
exercises the base client with the kwargs; the integration callers use 3
positional args against the override. Neither hits the production shape.

## Goals / Non-Goals

**Goals:**
- Restore full ingest on the deployed wiring (the exact call shape
  `pipeline.py` uses against the `SourceNeo4jClient` override).
- Keep the override's extra behavior (owner bank/tags lookup and properties)
  that the base implementation lacks.
- A test that fails with the current override and passes after the fix —
  pinned at the production call shape, not just the method in isolation.

**Non-Goals:**
- Refactoring the duplicate MERGE logic between base and subclass (both keep
  their own property sets; unifying is a separate cleanup).
- Backfilling `version_number`/`is_current` on pre-existing graph Document
  nodes — the MERGE only sets properties when a doc is (re)ingested; old nodes
  keep missing props and code reading them already defaults
  (`getattr`-style fallbacks at `backend.py:156-158`).
- Touching any caller or the base class signature.

## Decisions

**Extend the override's signature with defaulted kwargs, write the props in
its existing MERGE.**
`upsert_document_node(self, doc_id, title, file_type, overview="",
version_number=1, is_current=True)` — one extra SET pair
(`d.version_number = $version_number, d.is_current = $is_current`) in the
single statement it already runs. Alternatives considered:
- *Drop the override and let the subclass inherit the base implementation:*
  loses the bank/tags enrichment the source projection needs for read-side
  visibility filtering — would silently change graph visibility semantics.
- *Have the override call `super().upsert_document_node(...)` first, then run
  its own MERGE for bank/tags:* two round-trips per document node instead of
  one, for no behavioral gain.

**Test the subclass directly with a fake driver session** (same style as
`tests/engine/test_versioning.py`), asserting the kwargs are accepted and both
the version props and bank/tags props appear in the query/parameters. The
owner lookup needs a session factory stub returning a Document with bank/tags
— mirror how existing unit tests fake the async session.

## Risks / Trade-offs

- [Override and base drift again on the next signature change] → the new test
  pins the production call shape (kwargs) against the subclass, so adding a
  parameter without updating the override reddens immediately.
- [Old graph Document nodes lack version props until re-ingested] → acceptable:
  readers already default missing props; the nodes in production today were
  written by the override's bank/tags MERGE anyway.

## Migration Plan

Deploy via the normal release path (develop PR → maintainer release to main;
pipeline redeploys). After deploy, retry the one `failed` document left in
production from the 2026-09-12 upload via `POST /api/documents/{id}/retry` —
its upload file is intact. No data migration; no rollback beyond reverting
the commit (the failure mode it fixes leaves Postgres rows `failed`, which
retry heals).

## Open Questions

(none)
