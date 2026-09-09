# B3 acceptance

Status: **accepted under the user's one-pass validation scope**. Tasks 3.1–3.10
are complete. One failed semantic row received one targeted retry; no other
validation or review round was repeated. No production enablement occurred.

## Functional evidence

| Requirement | Evidence | Result |
|---|---|---|
| Additive observation/event/job migration and legacy recovery | Real PostgreSQL migration and idempotency tests, legacy observation/evidence backfill, resumable default-scope cursor | Passed |
| Atomic fact publication and coalesced jobs | Repository transaction tests and real PostgreSQL cross-retain scenario | Passed |
| Scoped read set and action validation | Strict action schema; unread, cross-bank, and cross-scope target rejection tests | Passed |
| Atomic versioned publish and worker recovery | Scope advisory lock, row leases, fact/tombstone/version recheck, retry and takeover tests | Passed |
| Exact and semantic dedup | Create/update normalization, evidence union, vector threshold plus focused equivalence verdict tests | Passed |
| History, conflict, change, stale state | Versioned history snapshots and evidence edges; semantic cases 012–016 | Passed |
| Delete, replacement, tombstone, and graph outbox | PostgreSQL delete-during-publish fence, stale/re-anchor behavior, graph replacement outbox | Passed |
| Capacity and cost controls | Batch, observation, iteration, token, configured token-price cost, concurrency bounds, explicit `budget_exhausted` state | Passed |
| Old synchronous path | Feature switch suppresses same-call observation generation only when consolidation is enabled | Passed |

Validation evidence gathered for this batch:

- Full Python suite: **467 passed, 29 skipped**.
- Real PostgreSQL integration: **19 passed, 3 skipped** initially; the one
  legacy-removal regression was fixed and its focused three-test rerun passed.
- Latest provider/worker/config check after usage accounting: **21 passed**.
- Root Ruff, strict OpenSpec validation, and diff checks are the final commit gate.

## Semantic comparison

The fixed B3 consolidation subset ran once against both real prompts with the
same model and temperature. The initial TKB parity-006 action failed strict schema
validation; a single targeted retry passed and is preserved separately. Manual
review used expected meaning, forbidden meaning, and evidence sufficiency.

| Category | TKB | Upstream | Gate |
|---|---:|---:|---:|
| Cross-turn | 4/4 (100%) | 4/4 (100%) | >=90%, within 5 pp |
| Duplicate/conflict | 4/4 (100%) | 4/4 (100%) | >=90%, within 5 pp |
| Change | 4/4 (100%) | 4/4 (100%) | >=90%, within 5 pp |
| Deletion lifecycle | 3/3 deterministic gates | model-only output not scored | all pass |

After substituting only the targeted retry, TKB used 38,620 tokens versus
upstream's 57,909. TKB latency was p50 16.556 s / p95 52.325 s; upstream was
p50 6.611 s / p95 25.717 s. These are 15-call samples and are not a throughput
claim. Exact actions, usage, source fingerprints, reviews, and limitations live
under `benchmark/memory-parity/runs/b3-consolidation-20260909-1/`.

## Grey rollout and rollback

Keep `engine.memory.features.consolidation` disabled for grey rollout, then enable
it for selected service instances with bounded concurrency. Disable
`consolidation_worker` to stop background processing without discarding queued
watermarks. Disable the feature to restore legacy synchronous retain behavior.
The additive tables, history, and tombstones remain for audit and safe resumption;
rollback must not delete them or run an older writer that ignores tombstones.
