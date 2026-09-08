# B1 implementation progress

Updated 2026-09-08. Current branch develop is based on original main f33759a2.
No implementation commit, push or deployment has been performed.

## B1 accepted: 12 of 61 tasks

Tasks 1.1–1.12 are checked. B2–B7 remain pending.

- 44 labelled cases in 11 categories and reproducible baseline fingerprints.
  Actual upstream/TKB LLM comparisons remain not_run.
- Default-compatible immutable scope/tag contracts, five tag modes and groups.
- Resumable bank migration, scoped uniqueness, source constraints and count checks.
- Protected memory scope_tags inherit document tags separately from source tags;
  file-type/session tags no longer invalidate exact visibility matching.
- Scope-bound memory repository, query/retain services, lifecycle hooks and queues.
  Background graph and conversation jobs restore durable bank and source tags.
- Hindsight Neo4j entities use bank plus normalized name. Ordinary GraphRAG stores
  descriptions per source document and checks PostgreSQL source visibility before
  graph aggregation. Vector filtering precedes top-k. Same-name cross-bank data,
  different-tag sources, foreign IDs and stale graph documents are tested.
- Document uploads, list/detail/raw text/edit/reindex/delete preserve/check scope.
  Generated files and Slidev downloads also check their artifact manifest scope;
  old manifests without scope remain default-team.
- Scope policy settings, effective versions, concurrent compare-and-swap and
  feature dependency validation. Flags remain off in the deployment configuration.

## Pi session boundary accepted

BFF/MCP credentials are resolved per request. BFF forwards only the validated
scope credential; Pi independently checks its configured digest map. The complete
canonical binding determines persistent session/transcript/tool-library directories.
Default sessions retain the old paths. Every session has its own MCP client and
memory extension; automatic recall and completed-turn enqueue use the same client.
Authorization occurs before SSE and session mutations. Durable ownership markers
allow explicit forgetting after ordinary history deletion and restart.

HTTP tests with real SDK sessions cover concurrent A/B memory injection and enqueue,
same-bank different bindings, default history compatibility, foreign IDs across
all lifecycle operations, credential rotation/revocation, restart and forgetting.
The model endpoint and MCP operations are deterministic test doubles; they verify
routing and persistence, not upstream semantic quality or provider availability.

## Verification

- uv --cache-dir .cache/uv run --no-sync ruff check: passed.
- uv --cache-dir .cache/uv run --no-sync pytest tests src/engine/hindsight_components/tests -q --basetemp=.cache/pytest-scope-b1-pi-final: 410 passed, 15 skipped.
- With RUN_INTEGRATION=1, SCOPE_TEST_DSN and SCOPE_TEST_NEO4J_URI targeting independent
  test containers: uv --cache-dir .cache/uv run --no-sync pytest tests/integration/test_memory_scope_migration.py -q --basetemp=.cache/pytest-scope-b1-pi-services: 9 passed.
- Real tests cover migration/resume/counts, tag truth tables, policy update races,
  PostgreSQL reads/writes, queue/worker ownership, Hindsight graph projection,
  ordinary graph/vector filtering, HTTP BFF and stateful MCP isolation, document
  lifecycle hooks and compatible scope-flag rollback without dropping owned data.
- Additional BFF lifecycle checks: pytest tests/frontend/test_bff_agent.py -q --basetemp=.cache/pytest-pi-lifecycle: 21 passed (six new scope cases).
- Pi npm run check: local dependency gate, typecheck, 91 tests and build passed.
- SPA npm test: 20 passed.
- Compose config verifies optional binding map propagation to both services; no deployment performed.
- openspec validate align-hindsight-memory-capabilities --strict: passed.
- git diff --check: passed.

The disposable PostgreSQL/Neo4j services are independent of LAN compose. Each
integration test removes only its generated schema or graph bank prefix. No
production migration or LAN compose operation was performed. The existing user
change to docker-compose.yml is preserved.

Operation details and legacy graph provenance handling: docs/memory-scope.md.
Old aggregated graph descriptions require all sources to remain visible; edited
aggregates are stale until source documents are reindexed. Compatible rollback
keeps scope-aware code and data; returning to a pre-scope reader is unsupported.

After verification, both owned --rm test containers were stopped and removed.
