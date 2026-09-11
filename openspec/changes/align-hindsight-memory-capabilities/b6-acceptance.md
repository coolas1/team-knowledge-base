# B6 acceptance

Status: **accepted under the user's focused validation scope**. Tasks 6.1–6.8
are complete. The legacy reflection path remains the default-compatible fallback;
the adaptive path is enabled only by the dependent feature flag.

## Delivered behavior

| Requirement | Evidence | Result |
|---|---|---|
| Scoped tool contract | Strict JSON contract permits only `search_mental_models`, `search_observations`, `recall`, `expand`, and `done`; every adapter uses the already scope-bound recall/repository instance | Passed |
| Adaptive state loop | Each tool result is returned to the planner before the next decision, so newly discovered gaps can cause further searches; valid mental models can be searched before raw recall | Passed |
| Unified bounds | Planner and tool calls share one monotonic deadline, estimated token ceiling and maximum iteration count; failures retain collected evidence and terminate with an explicit insufficient-evidence answer | Passed |
| Freshness backcheck | Stale mental models cannot be cited as current; stale observations must be expanded and current source facts become independently validated citation candidates | Passed |
| Citation validation | Retrieved candidates and `actual_citations` are separate; memory citations are re-expanded in the scoped repository immediately before completion, unknown/deleted/stale IDs are rejected, and only one repair is allowed | Passed |
| Trusted directives | Directives have an independent scoped table, CRUD service, active/priority/trigger fields, and legacy migration; ordinary memory text never enters the trusted directive loader | Passed |
| B5 refresh integration | Mental-model refresh optionally uses adaptive reflection and accepts only its validated current memory citations; both generation paths share the B5 CAS/source-version/tombstone publication fence | Passed |

## Focused validation and comparison

- Adaptive/legacy reflection, query, mental-model and model tests: **20 passed**
  before the final added budget, stale-observation and adaptive-refresh cases; the
  exact new cases then passed.
- Disposable PostgreSQL: **2 passed** for B5 publication/deletion fencing and B6
  directive migration, trigger matching, scope isolation and separation from an
  instruction-shaped conversation memory.
- Fixed B6 contract corpus ran once for 25 repetitions (100 total scenarios):
  semantic contract accuracy **100%**, failed cases **0**, mean tool calls **3**,
  p50 **0.0572 ms**, p95 **0.0797 ms**. These timings measure local control-flow
  overhead with deterministic providers; B7 retains the end-to-end upstream/TKB
  model and service comparison.
- Raw report: `benchmark/memory-parity/runs/b6-reflect-20260909-1/report.json`.

The four B6 corpus cases cover newly exposed third-hop search, stale-model fact
backcheck, forged citation repair, and bounded termination despite instruction-like
memory content. The final commit gate runs focused tests, Ruff, strict OpenSpec
validation and diff checks once.

## Rollout and rollback

Enable `engine.memory.features.adaptive_reflect` only after the existing dependency
chain through mental models. Disable that flag to restore the legacy scoped
reflection path without removing directives, model versions or pending refresh
jobs. `mental_model_use_adaptive_reflect` separately controls whether B5 refreshes
use the new reasoner.
