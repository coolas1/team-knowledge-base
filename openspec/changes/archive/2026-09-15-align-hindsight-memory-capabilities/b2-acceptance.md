# B2 acceptance

Status: **accepted under the user's revised validation scope**. Tasks 2.1–2.10
complete. The user explicitly requested fewer validation/review rounds and faster
implementation. Acceptance uses the first complete post-fix repetition of all
eight planned semantic cases plus the functional/fault evidence below. Remaining
repetitions were stopped; no best-of selection or statistical parity claim is made.
No production enablement has occurred.

## Functional evidence

| Requirement | Current evidence | Result |
|---|---|---|
| Completed-turn intent survives process exit | Production TranscriptStore fsynced completion; independent Node seed/recovery processes | Passed |
| Network outage and acknowledgement loss | delivery-fault-smoke.mjs: unavailable loopback MCP, restart, exit 86 after remote ack, replay | Passed; receiver is an isolated durable test ledger |
| Actual MCP to PostgreSQL acceptance boundary | test_pi_process_replays_ack_through_production_mcp_to_postgres: production tool/service/queue, independent Pi processes, one queue row and stable operation/document IDs | Passed |
| Retry/cancel/fencing | Queue integration; test_expired_worker_process_cannot_publish_after_takeover uses a real one-second lease and independent old worker | Passed |
| Speaker/time provenance and extraction outcomes | Retain/provider/worker tests, actual source-time reprocess integration; JSON-mode HTTP 400 regression fixed | Contract passed; semantic gate below |
| Entity aliases, same names and manual corrections | Real PostgreSQL/Neo4j contextual entity/correction/reprojection tests, scoped candidate and deadline tests | Passed functional checks |
| Request keys and revision conflicts | Concurrent publication/replay/conflict/rollback integration, queued operation identity | Passed |
| Append/replace/cache/invalidation | Real append/replay/policy/time/replace/race tests; legacy random IDs, duplicate-block generations, unchanged external evidence | Passed |
| Old calls and chat failure isolation | Python contract suite; Pi 97 tests; flags remain off by default | Passed |

Latest validation:

- Python: `uv --cache-dir .cache/uv run --no-sync pytest tests src/engine/hindsight_components/tests -q --basetemp=.cache/pytest-b2-acceptance-current`: **458 passed, 27 skipped**. Skipped integrations are not counted as passed.
- Disposable PostgreSQL/Neo4j: `tests/integration/test_memory_scope_migration.py`, with explicit SCOPE_TEST_DSN/SCOPE_TEST_NEO4J_URI: **21 passed**, 25.20 seconds (`.cache/pytest-b2-e2e-full`).
- Pi: `npm run check`: local security gate, typecheck, **97 tests**, build passed.
- Actual socket/process drill: `.cache/delivery-process-fault-2/result.json`: both scenarios passed, two deliveries / one durable receiver record.
- Root `ruff check` passed. Strict OpenSpec validation and git diff checks passed.

## Semantic evidence

The baseline ran the fixed corpus's eight attribution/time cases three times on
each actual extraction implementation, using the same configured model. Revised
acceptance uses one complete post-fix repetition. These runs do not claim
whole-engine query/retention equivalence. Baseline verdicts are bound to exact
row hashes; the accepted repetition is recorded with its results-file hash.
HTTP success alone does not pass. Reasoning tokens are counted once despite the
different upstream/TKB usage conventions.

| Run | State | Findings |
|---|---|---|
| b2-extraction-20260909-1 | Complete, reviewed, failed | TKB attribution 100%, time 75%; user travel misclassified as experience in all 3 repetitions. Upstream 50%/100% under the fixed expected-clause rubric. |
| b2-extraction-actor-fix-20260909 | Complete diagnostic | Two TKB successes, one HTTP-200 local parsing degradation; failure retained. |
| b2-extraction-month-diagnostic-20260909 | Complete diagnostic | All 3 TKB outputs world; June 2025 range preserved. Earlier parse failure not reproduced. |
| b2-extraction-20260909-2 | Stopped on user instruction | First complete repetition: 16 paired outputs; TKB attribution 4/4 and time 4/4. Remaining partial outputs preserved. acceptance-scope.json records the selection and evidence hash. |

The prompt now explicitly distinguishes the described actor from the message
speaker and reserves experience for the memory-owning Agent. Cache schema v3
invalidates old extraction results. Baseline results remain unchanged.

## Release and remaining scope

Record this report with the local develop batch commit. Keep
unrelated docker-compose GPU edits out of the commit. No automatic push/deploy.
Schema updates are additive; retain a compatible writer when disabling features.
User-facing operation/entity management, ongoing consolidation/tombstones, expanded
recall, mental models and reflect tools remain their B3–B7 tasks. Full 44-case,
whole-engine comparison and migration/rollback rehearsals remain the B7 gate.
