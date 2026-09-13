## 1. Regression test (fails on current code)

- [x] 1.1 Add a unit test that calls `SourceNeo4jClient.upsert_document_node` with the exact production call shape from `pipeline.py:229` (`version_number=2, is_current=True` kwargs, fake driver + owner-lookup session factory) asserting the MERGE writes `version_number`, `is_current`, `bank_id`, `tags` — verify it fails with TypeError on the current override
- [x] 1.2 Add the superseded-version companion case (`is_current=False`, matching the `backend.py:771` call) asserting the parameter round-trips into the query parameters

## 2. Fix

- [x] 2.1 Extend `SourceNeo4jClient.upsert_document_node` (`src/engine/components/store/source_graph.py:82`) with `version_number: int = 1, is_current: bool = True` and add both properties to its single MERGE statement — verify the 1.x tests pass and no caller changed

## 3. Verification and rollout

- [x] 3.1 Run `uv run ruff check` and `uv run pytest` — both clean (the 3-positional-arg integration callers must still pass via the new defaults)
- [x] 3.2 Open a develop PR with the change; after maintainer merge + release, verify the pipeline redeploys and `GET /version` reports the new tag
- [x] 3.3 Production smoke: retry the `failed` doc from the 2026-09-12 upload (`POST /api/documents/588bd22f-ec98-40af-826f-f5d605d0cebc/retry`) and upload the second autopilot PDF — verify both reach `indexed` with chunks and graph nodes written (no `version_number` TypeError)
