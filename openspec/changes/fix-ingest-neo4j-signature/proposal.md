## Why

Every full ingest on v0.3.0 fails at the Neo4j write stage: the versioned-documents
change taught `pipeline.py` and `backend.py` to pass `version_number`/`is_current`
to `upsert_document_node` and updated the base `Neo4jClient`, but the
`SourceNeo4jClient` override — which production always wires (`backend.py:1014`)
— kept the old 4-parameter signature. First production hit 2026-09-12: a PDF
upload extracted 8 chunks, then died with
`TypeError: SourceNeo4jClient.upsert_document_node() got an unexpected keyword
argument 'version_number'` and the doc row ended `failed`. It went unnoticed
because no full pipeline run happened between the v0.3.0 deploy and that upload
(all later re-uploads deduped to no-ops).

## What Changes

- `SourceNeo4jClient.upsert_document_node` accepts `version_number: int = 1`
  and `is_current: bool = True` and writes them as properties on the
  `Document` node in the same MERGE that already writes `bank_id`/`tags`.
- Unit tests covering the override with the production call shape (kwargs
  from the pipeline), closing the gap where base and subclass were each tested
  in isolation and the wiring was not.

No caller changes; no schema changes; defaults keep the existing 3-positional-arg
integration callers working.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `ingest`: adds a requirement that the graph-write stage completes on the
  client implementation production actually wires, and that Document nodes
  carry the version-chain properties.

## Impact

- `src/engine/components/store/source_graph.py` (the override).
- `tests/engine/` — new unit test for the override's signature and written
  properties.
- Production data repair after deploy: retry the one `failed` doc left by the
  2026-09-12 upload (its upload file is intact, so the existing
  `POST /api/documents/{id}/retry` path applies — no code needed).
