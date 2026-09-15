# B7 acceptance

Status: **accepted under the user's one-pass validation scope**. Tasks 7.1–7.8
are complete. This batch used one consolidated project gate and one real PostgreSQL
migration drill. Only the migration compatibility failure received a targeted
rerun. No production deployment, push, or OpenSpec archive was performed.

## Management behavior

| Requirement | Evidence | Result |
|---|---|---|
| Operation diagnostics | Scoped list/detail by session and turn combines conversation delivery, retain, consolidation and model-refresh stages under a durable operation ID; retry/cancel clears leases and fences stale workers | API, CLI and MCP implemented; real PostgreSQL scope/lease scenario passed |
| Fact, source and history management | Fact list exposes provenance without raw source payload; observation detail returns version history and only current active sources; entity correction uses the existing scoped audit/rebuild path | BFF and PostgreSQL deletion/history tests passed |
| Diagnostic frontend | `/memory` shows stage/status/error, retry/cancel, fact freshness, a clickable document source link and observation history | TypeScript build, API and view-model tests passed |
| Policy/model/directive frontend | The page validates required model/directive fields and JSON-object policies, publishes policies with expected-version CAS, displays model version/freshness/evidence and supports refresh/delete | BFF validation, API/view-model tests and production build passed |

## Full fixed comparison

`benchmark/memory-parity/runs/b7-full-20260909-3/report.json` covers all 44 fixed
cases and both engine columns. Twenty-three cases (46 rows) are immutable real
upstream/TKB calls with the same configured model. The other 21 TKB rows use real
PostgreSQL/process or production-contract gates; the 21 upstream rows execute the
pinned lifecycle code paths and have no invented model timing or token values.

- All 11 categories: TKB **4/4 (100%)**; every gate is >=90% and no worse than the
  pinned upstream result. Six unsupported upstream expectations are explicit gaps,
  rather than assumed passes.
- Direct model execution failures: TKB **0/23**, upstream **0/23**.
- Upstream direct-model p50/p95: **9.000/34.812 s**, **41,357 tokens**.
- TKB direct-model p50/p95: **14.891/52.325 s**, **60,569 tokens**.
- Corpus SHA-256 remains
  `e4534bd86bada0c28f1a1061823ff47772ac4e955a46eb291b027522530178e3`.

The report generator validates the accepted B2/B3 artifacts, B6 contract report,
migration report, the executed upstream contract report, corpus cardinality and
both git revisions before publishing.

## Migration and rollback drill

The disposable PostgreSQL run selected ten scenarios for historical delivery and
append replay, request/revision idempotency, resumable scope backfill and counts,
fresh/legacy migration, observation history and tombstones, model deletion fencing,
directive separation, operation scoping and compatible feature rollback.

The first run passed 9/10. The failure showed that a preceding-version direct SQL
writer could omit the newly required `mental_models.source_query` on a fresh
schema. A server default was added to both ORM DDL and additive migration. Only
that case was rerun: **1/1 passed**. The immutable drill record is
`benchmark/memory-parity/runs/b7-migration-20260909-1/report.json`.

## Final project gate

- `ruff check .`, `git diff --check`: passed.
- `pytest tests src/engine/hindsight_components/tests`: **486 passed, 32 skipped**.
  Skipped integration tests are not counted as evidence for the database drill.
- Selected real PostgreSQL drill: final **10/10 passed** after the one targeted
  compatibility rerun.
- Frontend Vitest: **24 passed**; TypeScript/Vite production build passed. Vite's
  existing large-chunk advisory remains non-blocking.
- Fixed corpus validation and strict OpenSpec validation: passed.

The CI/CD runbook now documents dependency-order enablement, operation checks,
scope expansion and scope/tombstone-safe rollback. The pipeline remains the sole
LAN operator.

## Spec review and exclusions

The final review covers 27 requirements across all nine delta specs:
conversation-memory (5), ingest (2), memory-consolidation (4), memory-operations
(3), memory-reflection (2), memory-retention (4), memory-retrieval (2),
memory-scope (3), and mental-models (2). Batch evidence B1–B7 maps each requirement
to functional, semantic or migration results.

`docs/hindsight-capability-matrix.md` records the resulting Hindsight/TKB mapping.
Explicit follow-up scope remains Hindsight SDK wire compatibility, all third-party
integrations, every optional search backend, and future upstream revisions. These
are proposal exclusions rather than failed core gates.
