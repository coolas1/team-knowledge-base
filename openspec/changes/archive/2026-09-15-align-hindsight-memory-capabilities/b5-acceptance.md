# B5 acceptance

Status: **accepted under the user's focused validation scope**. Tasks 5.1–5.7
are complete. Validation used one focused pass per affected surface and one real
PostgreSQL scenario; only concrete failures were rerun.

## Delivered behavior

| Requirement | Evidence | Result |
|---|---|---|
| Scoped definition and CRUD | Public query contract and MCP tools create, list, read, update and delete explicit definitions; repository predicates bind bank and trusted tag visibility | Passed |
| Legacy migration and versions | Existing summaries migrate to version 1 with source question and active freshness; empty models migrate to version 0; immutable version rows retain source IDs, source versions, usage and cost | Passed |
| Manual full refresh | Durable model job calls the B4 recall engine with current source facts and bounded evidence, then uses the configured project provider without depending on B6 | Passed |
| Relevant event refresh | Successful scoped consolidation coalesces one job per model and source watermark; model tags must be contained by the consolidation write scope | Passed |
| Scheduled refresh and recovery | Due intervals are discovered from database time, missed schedules enqueue after restart, expired leases recover, and attempts are capped | Passed |
| Structured delta fallback | Strict replace/append/delete operations require the current base version and exact targets; malformed deltas perform one bounded full refresh | Passed |
| Failure and deletion safety | Failed generation retains the last successful head and marks it stale; source deletion marks dependent models stale, queues recomputation, revokes an in-flight lease, and publication rechecks source versions and tombstones | Passed |

## Focused validation

- Mental-model, model mapping and query unit tests: **14 passed**.
- Affected repository, consolidation, query and MCP tests: **65 passed** before
  two legacy repository doubles exposed a missing default for `memory_version`;
  the compatibility fallback was added and the exact failures passed.
- Frontend startup and configuration follow-up: **29 passed** after moving the
  pytest temporary directory into the writable project tree.
- Disposable PostgreSQL acceptance: **1 passed**. The single scenario exercises
  legacy migration, scope isolation, manual job coalescing, immutable version
  publication with evidence and cost, duplicate event coalescing, deletion during
  refresh, last-success retention, stale diagnostics and missed schedule recovery.
- Focused Ruff and diff checks pass. Strict OpenSpec validation is the commit gate.

The five fixed B5 corpus cases (`parity-031`, `parity-037`–`parity-040`) map to
deterministic assertions in the PostgreSQL scenario and delta worker tests. The
full upstream/TKB semantic comparison remains the single B7 comparison run, as
required by the efficiency-adjusted design.

## Rollout and rollback

The worker is gated by `engine.memory.features.mental_models` and
`engine.memory.mental_model_worker`; the feature dependency chain requires B1–B4.
Definitions and successful versions remain readable when the worker is disabled.
Rollback disables the worker first, then the feature. Existing model/version rows
are additive and do not change legacy recall behavior.
