# B4 acceptance

Status: **accepted under the user's focused validation scope**. Tasks 4.1–4.6
are complete. Validation ran once per affected surface; only concrete failures
received focused reruns. No production enablement occurred.

## Delivered behavior

| Requirement | Evidence | Result |
|---|---|---|
| Extended recall contract | Public DTO, BFF and MCP accept type/source/tag/reference-time/min-score/preference/include/budget fields; old defaults remain unchanged | Passed |
| Uniform bounded retrieval | Semantic, BM25, graph and temporal repository calls receive one immutable filter; result/candidate/token/deadline requests are intersected with configured hard maxima | Passed |
| Safe expansion | Scoped `expand_memory` revalidates indexed source and active/stale memory, omits tombstoned facts, and bounds combined chunk/document/fact text | Passed |
| Freshness and evidence | Recall returns mentioned/updated time, freshness, stale reason and optional current source facts; stale results cannot appear as current | Passed |
| Pi injection controls | Optional type/source-time labels and memory-type request; escaped untrusted block stays temporary and absent from retained visible history | Passed |
| Compatibility and faults | Existing fast/deep, degraded-arm, adapter, conversation and GraphRAG fallback tests remain compatible | Passed |

## Validation and comparison

- Python default discovery: **299 passed, 29 skipped**.
- Hindsight component discovery: **173 passed, 2 failed** initially because two
  legacy test doubles lacked the new timestamp/state attributes. The compatibility
  fallback was added and the exact two failures then passed. Other component tests
  had already passed in that run.
- Pi full `npm run check`: security gate, typecheck, **18 files / 99 tests**, and
  production build passed.
- Disposable PostgreSQL: consolidation/source-delete/freshness/expand scenario
  passed after fixing its fixture provenance; scoped type/tag/reference-time filter
  scenario passed. Deleted and cross-bank IDs returned no expanded content.
- Root Ruff, strict OpenSpec validation, and diff checks are the commit gate.

The deterministic production-core comparison used 25 calls per path. Legacy fast
recall measured p50 **0.837 ms**, p95 **0.938 ms**; extended filtered recall measured
p50 **0.791 ms**, p95 **0.934 ms**. Both selected a mean 30 output tokens and used
zero model tokens. This isolates request-contract overhead and is not an upstream
relevance benchmark; B7 retains the full end-to-end comparison gate. The raw report
is `benchmark/memory-parity/runs/b4-retrieval-20260909-1/report.json`.

## Grey rollout and rollback

New request fields are optional. Roll out by allowing selected callers to request
filters and expansion while leaving old requests untouched. Server maxima remain
authoritative. Roll back callers by omitting the new fields and disable Pi labels
with their three `TKB_CONVERSATION_MEMORY_*` settings. No schema migration is
required for B4; B3 freshness/evidence tables remain the source of truth.
