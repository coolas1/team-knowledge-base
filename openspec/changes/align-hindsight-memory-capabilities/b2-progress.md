# B2 implementation progress

Current: tasks 2.1–2.10 complete; overall 22/61. B2 accepted using the user's
revised efficient validation scope; see b2-acceptance.md. Entries below are history.

- assistant.completed includes durable delivery intent in the same fsynced event.
  Scope/session/turn identity and exact request content fingerprints survive restart.
- A bounded per-scope scanner persists acknowledgements, retry backoff, attempt
  limits and sanitized conflict/failed status. Incomplete and pre-feature historical
  turns are not scanned. Health exposes local delivery separately from engine jobs.
- Engine repeated submissions lock and compare existing content; identical requests
  preserve the original document, changed content conflicts. Optional acknowledgement
  returns only after the queue transaction commits and includes the request hash.
- Deleting history first archives only undelivered turns; acknowledged archives are
  removed. Explicit forgetting cancels local pending delivery. Full in-flight
  deletion tombstones and derived-data propagation remain B3 work.
- Private unattended restart requires PI_AGENT_MEMORY_DELIVERY_TOKENS to supply a
  configured credential for each isolated binding. Tokens stay in process/env only;
  startup rejects missing mappings instead of silently delivering into default-team.
- New runtime flag defaults off. No production flag, credentials or deployment changed.

Verification:
- Pi npm run check: local dependency gate, typecheck, 97 tests and build passed.
- Python full regression: 417 passed, 15 skipped; tests/agent/test_mcp.py: 31 passed.
- Independent PostgreSQL/Neo4j tests: 9 passed, including concurrent duplicate enqueue
  and different-content conflict without overwriting the stored original.
- Tests simulate loss of local acknowledgement, reopen durable stores, provider/MCP
  failure, retry limits, wrong scope, deletion archival and private startup recovery.
  They do not replace the real process/network fault and semantic acceptance in 2.10.

Remaining: operation stages and lease fencing (2.3), extraction identity/time and
outcomes (2.4–2.5), entity resolution/correction (2.6–2.7), revision and incremental
retention (2.8–2.9), then B2 acceptance (2.10).

Earlier checks: ruff, strict OpenSpec validation and git diff --check passed. Develop
contains main. Disposable containers were restarted for the following verification.

## 2026-09-08 continuation: leases and extraction outcomes

- Added durable operation IDs, stage results and lease tokens/deadlines with a
  repeatable additive migration. Pi stores the accepted operation ID.
- Memory replacement and state writes check and lock the current lease inside the
  publishing transaction. Stale completion, failure and cleanup cannot overwrite a
  successor. Expired final attempts become failed; explicit retry preserves operation
  identity and cannot revive cancelled or running work.
- Source time, timezone, actor/speaker bindings and policy version flow through Pi,
  MCP, queue and retain. Relative dates use the source local day; missing anchors
  remain unknown. Added tzdata for portable Windows timezone support.
- Extraction now reports success/empty/degraded/failed. Failed extraction preserves
  source chunks without inventing facts. Workers retry incomplete results; historical
  backfill reports them as incomplete. Added extraction retry entry points.
- At that checkpoint tasks 2.4–2.5 remained unchecked pending entry-point/context and semantic
  verification. Parser tests preserve user/assistant/unknown and suggested/completed
  distinctions; they do not demonstrate actual LLM attribution accuracy.

Verification: Python tests plus component tests 432 passed, 16 skipped; Pi check
(security gate, typecheck, 97 tests, build) passed. Independent PostgreSQL/Neo4j suite
10 passed before extending retry assertions. Extended lease/retry/cancel test then
passed separately after fixing mixed host/database time in immediate retry scheduling.
Ruff and strict OpenSpec validation passed. No deployment or semantic parity claim.

## Context/reprocessing verification and entity candidate groundwork

Tasks 2.4–2.5 now pass implementation checks. Real-model semantic comparison remains
explicitly in 2.10; scripted extraction responses verify contracts, not model accuracy.

- Trusted enqueue identity and policy, invalid/naive timestamps, original local-day
  grounding, suggested/completed modality and unknown identity are covered.
- Missing fact types and reversed event dates are invalid output. Agent-name fallback
  now agrees between prompt context and persisted speaker ID.
- Document state stores source context with repeatable DDL. Successful, degraded and
  build-failed retention preserve it; file reprocessing restores original time and
  speakers while using the current scope policy version. Private source replay is
  denied outside its tags. Conversation reprocessing remains on the durable queue.
- MCP extraction retry validates IDs and sanitizes provider/storage failure. Tests
  cover scheduling, invalid IDs and errors; worker and backfill incomplete results
  remain distinct from successful retention.
- 2.6 is in progress: bounded EntityResolver and repository candidate lookup added.
  Alias evidence is read from visible source facts, not global entity metadata.
  Tests cover contextual alias match, distinct same-name identity, unknown/invalid
  decisions, unknown target IDs and enforced timeout. Candidate/replay PostgreSQL
  integration passed. Entity identity persistence, retain wiring and correction
  migrations are still pending; this is not yet active entity disambiguation.

Latest Python regression: 448 passed, 17 skipped. Full independent PostgreSQL/Neo4j
integration: 11 passed, including file replay and alias visibility with a provider
fixture matching the database embedding dimension. Ruff, strict OpenSpec validation
and git diff --check passed. No Pi/UI code changed
in this continuation. Develop remains the working branch; no production changes.

## Entity persistence and correction (2.6–2.7)

- Retain now optionally resolves scoped name/alias candidates against fact and source
  context before publication. A shared 15-second entity-stage deadline and bounded
  candidate lists prevent unbounded model work. Invalid/timeout decisions preserve
  separate identity and report degraded; unknown evidence never implies a match.
- Added identity_key without changing existing entity IDs; legacy records use the
  empty key. Same bank/name can hold distinct resolved identities. Source mention
  names and aliases are stored on the fact/entity association, so alias retrieval
  cannot borrow hidden source evidence. Persisted facts keep original entity_mentions.
- Publication rechecks visible matched entities inside the document transaction.
  Within-plan and cross-document matches use resolved IDs to build entity edges.
- Added HindsightService.correct_entity for explicit scope-checked reassignment or
  splitting of 1–100 source mentions, with a reason and append-only correction record.
  It preserves names, replaces affected entity edges and queues affected documents
  for graph projection in the same transaction. Management API/UI remains B7.
- Neo4j now keys HindsightEntity by ID and indexes bank/name without uniqueness;
  otherwise PostgreSQL disambiguation would be merged away in the graph.
- engine.memory.features.entity_resolution defaults off and requires reliable_retention
  (and scope). B2 acceptance is still required before enabling production use.

Evidence: Python regression 449 passed, 19 skipped; independent PostgreSQL/Neo4j
suite 13 passed. Tests cover legacy-column upgrade, repeat migrations, alias linking,
same-name separation, reassignment/split, original names, scoped denial, relation
rebuild and graph identity after reprojection. These use controlled provider outputs;
real LLM semantic accuracy remains unverified in 2.10. Ruff passed. No deployment.

## Revision and stable-reference foundation (2.8 in progress)

- Added retention revision to document memory state with repeatable migration and
  optional expected_revision on retain input/result. The revision is read before
  extraction and compared under the document transaction lock at publication.
  Competing plans from the same revision cannot both publish.
- Failed build state updates use the same revision check. Hook/backfill conflict
  handling leaves newer state intact. Entity corrections advance affected document
  revisions so already-running extraction cannot overwrite a correction.
- Chunk IDs derive from document/content/duplicate occurrence; source and fact IDs
  derive from the chunk and semantic fact fields; observation IDs derive from sorted
  evidence IDs and text. Repeated equivalent extraction keeps references stable.
- This is the retention revision, not a completed append/replace source-edit protocol.
  Request-id persistence/conflict handling, extraction cache and preservation of
  unchanged persisted rows remain pending in 2.8–2.9. Both tasks stay unchecked.

Verification: Python 450 passed, 20 skipped; full independent PostgreSQL/Neo4j
suite 14 passed. New tests verify stable replay IDs, concurrent publication CAS,
stale failure fencing and scoped revision reads. No production flags changed.

## Request ledger (2.8 completed)

- Optional request_id identifies a retain request within its owned document. A
  canonical fingerprint covers content, extraction context, metadata, policy and
  expected revision. Reusing an accepted key with different input conflicts.
- retention_requests stores the fingerprint and typed result in the same transaction
  as memory publication, revision and graph outbox. Document deletion cascades the
  ledger; request replay still verifies current document scope before reading it.
- Replays return the original committed result before extraction or revision checking.
  Concurrent identical submissions may both extract, but only one publishes; the
  second returns the first result under the document lock without advancing revision.
- Results reflect the original operation, including degraded status; repeating an
  accepted key does not reprocess it. Explicit extraction reprocessing starts new work.
  A failure before publication does not leave a committed request record.
- HindsightService.retention_revision exposes the scoped revision for callers.
  Append/replace preparation, extraction reuse and preserving unchanged rows remain
  in 2.9, which stays unchecked.

Verification: Python regression 450 passed, 20 skipped before the added integration
case; full independent PostgreSQL/Neo4j suite 15 passed. The extended request test
also passed separately after injecting a failure before commit: original revision
and memory remain, and no request ledger entry survives. Concurrent same-key replay,
changed-content conflict and scope denial are covered. No live deployment changes.

## Extraction cache (2.9 in progress, 2026-09-09)

Validated extraction payloads are cached by chunk content, extractor schema version,
source kind/title/context, trusted source time/speakers and policy version. The cache
is scoped to its document and replaced atomically with publication, so removed chunk
payloads are not retained indefinitely. Provider/schema failures do not enter cache.
`force_extraction` bypasses lookup; explicit file reprocessing uses it automatically.
Unchanged content skips extraction, while policy changes cause re-extraction.

Append preparation and preservation of unchanged persisted rows are still pending;
2.9 remains unchecked. Latest Python regression: 450 passed, 22 skipped. Independent
PostgreSQL/Neo4j suite: 16 passed, including reuse, policy invalidation, scope denial,
failure recovery and forced extraction. This state is being saved as a checkpoint
at the user's request; B2 acceptance is not complete.

## Preserve unchanged rows (2.9 continuation)

Publication now computes retained/removed memory IDs and deletes only removed IDs.
Unchanged rows keep their original mentioned_at, inbound references and entity
corrections while allowing position/metadata updates. Derived observations are
invalidated only when an actual source ID is removed. Planned entity edges are
checked against persisted mention ownership, so replay cannot bypass a correction.

The document/chunk/memory position constraint is migrated to DEFERRABLE INITIALLY
DEFERRED, allowing stable IDs to exchange positions within one transaction. The
upgrade from the old immediate constraint and a two-chunk swap are tested against
PostgreSQL. Another-document observation and incoming evidence edge survive the
swap; removing one source invalidates the observation. Corrected entity ownership
also survives a subsequent retain and Neo4j reprojection.

Validation: Python 450 passed, 23 skipped; existing 16-case real PostgreSQL/Neo4j
suite passed, followed by targeted passes for the new row-preservation/constraint
upgrade case and extended correction/replay case. Ruff passed. Append/replace
source preparation is still pending and 2.9 remains unchecked. These edits follow
checkpoint eb31487c; the next batch commit waits for B2 completion as requested.

## Durable content snapshot (2.9 continuation)

Retain now publishes a versioned content/chunk snapshot atomically with memories,
request acceptance, revision and extraction cache. Each chunk records its original
source timestamp, timezone, speakers and metadata. Snapshot restoration preserves
these anchors while selecting the current policy version. A scoped repository read
returns snapshot and revision in one SQL statement, preventing mixed-version reads
when preparing an append. The additive migration distinguishes missing legacy
snapshots from explicitly retained empty content.

Validation: Python suite 452 passed, 23 skipped (`.cache/pytest-content-snapshot`);
all 17 real PostgreSQL/Neo4j cases passed (`.cache/pytest-snapshot-live`), including
snapshot publication and cross-bank denial. Ruff and diff checks passed. Append
preparation, per-chunk extraction/build context and reprocess integration still
need wiring; task 2.9 remains unchecked and B2 has not been accepted.

## Append and provenance wiring (2.9 continuation)

The service now accepts explicit `update_mode=append|replace` (default replace).
Append requires a request ID and fingerprints the original request before merging
content. It restores old chunks and appends independently chunked new content;
extraction cache keys and fact construction use each chunk's original provenance.
Extract-stage reprocessing restores the saved chunks even when Document.raw_text
differs, preserving stable references and source dates. The default replace hash
remains compatible with request ledger entries created before update_mode existed.

Validation: existing Python suite 452 passed, 23 skipped; full real-service suite
18 passed (`.cache/pytest-append-live`). Expanded append-policy test then passed
separately (`.cache/pytest-append-policy-live`), proving a policy bump re-extracts
both old and new chunks with their original timestamps. Snapshot/validation/hash
tests: 3 passed. Ruff passed. Still pending: concurrent append-specific tests,
legacy snapshot transition, and replace boundary reuse after append. Task 2.9
remains unchecked; these results are functional tests, not LLM semantic acceptance.

## Replace boundaries and concurrent append (2.9 continuation)

Replace now preserves exact snapshot boundaries (including overlapping chunks)
when the full content is unchanged. For edits it reuses complete old blocks at
line boundaries and chunks only intervening new text. Matches inside changed
sentences cannot inherit old source provenance. Unit coverage checks original
speaker attribution and current policy selection for reused versus new blocks.

Real PostgreSQL coverage confirms that replacing appended content does not invoke
extraction, changing its first block extracts only that block, and concurrent
append requests using the same revision cannot overwrite each other. The rejected
request can retry at the new revision; both additions then occur exactly once.
Python tests: 455 passed, 24 skipped (`.cache/pytest-replace-blocks`). Expanded
append/replace/race test passed (`.cache/pytest-append-race`); Ruff passed. Legacy
snapshot reconstruction remains pending, so 2.9 and B2 acceptance remain open.
Full PostgreSQL/Neo4j regression subsequently passed all 18 cases
(`.cache/pytest-replace-race-full`, 19.23 seconds); OpenSpec strict validation passed.

## Legacy snapshot recovery (2.9 continuation)

Scoped snapshot reads now share the document publication lock while recovering
legacy source rows and their revision. Recovery uses retained source blocks when
the ordinary file has newer, unretained content; absent timestamps/speakers remain
unknown. Stored semantic fields map matching source/fact rows back to their old
random UUIDs, preserving inbound references across the first upgraded publication.
Legacy extraction is not treated as a successful modern cache entry without fresh
validation; subsequent accepted snapshots and request replays use normal caching.

Validation: Python 455 passed, 24 skipped (`.cache/pytest-legacy-snapshot-unit`);
all 19 real PostgreSQL/Neo4j cases passed (`.cache/pytest-legacy-snapshot-live`,
20.33 seconds). The new case proves old random IDs and original retained content
survive append despite a different raw file, and replay performs no extraction.
Ruff passed. Before closing 2.9, audit duplicate-block identity stability when
replacement removes or inserts an identical occurrence. B2 semantic acceptance
remains pending and no batch-completion commit has been made.

## Incremental retention complete (2.9)

Accepted snapshots now persist chunk and semantic fact identity maps for all
blocks, not only recovered legacy data. Retained blocks keep their IDs across
position changes. New blocks incorporate the publication generation, preventing
a later identical addition from reusing the identity of a removed block. When
replace receives indistinguishable duplicate text it deterministically preserves
the first saved occurrence; explicit append creates a distinct occurrence.

Validation: Python 455 passed, 25 skipped (`.cache/pytest-persistent-chunk-ids`);
all 19 real PostgreSQL/Neo4j cases passed (`.cache/pytest-persistent-chunk-live`,
23.59 seconds). Expanded legacy test verifies distinct duplicate block/fact IDs,
consistent retention on duplicate removal, and fresh identity on a later append.
Together with preceding cache, concurrency, request replay, provenance, migration,
rollback and source-reference tests, this completes task 2.9. Progress: 21/61.
Task 2.10 still requires batch fault evidence and three actual upstream/TKB semantic
runs; the fixed corpus remains not_run. B2 is not yet accepted or batch-committed.

## B2 extraction acceptance runner (2.10 in progress)

Added an extraction-only adapter that loads actual upstream extraction/config/LLM
modules while bypassing service package initializers. The runner selects B2
attribution/time cases, removes orchestration timestamps from source text, supplies
source time through each engine's API, and fsyncs per-case results plus SHA/corpus/
adapter fingerprints. This does not replace retain/storage/fault parity execution.

Runtime probe successfully loaded upstream extract_facts_from_text. Provider setup
requires additional upstream dependencies: OpenTelemetry API/SDK/Prometheus and
OpenAI SDK were installed under `.cache/parity-deps`, without changing application
dependencies. Latest smoke attempt `.cache/parity-extraction-smoke-4/manifest.json`
records setup_failed because upstream eagerly imports the Gemini provider and
google.genai is missing. No model result or semantic pass has been produced yet.
Next: finish isolated dependency setup, capture usage/effective configurations and
untracked implementation fingerprints, then run and review all three repetitions.

## Actual model smoke and three-repeat run started (2.10)

The isolated dependency environment now supports normal upstream package imports;
the adapter no longer bypasses package initializers. Smoke run 10 completed both
upstream and TKB extraction for parity-001. TKB initially received HTTP 400 because
the endpoint requires the word JSON in a message when json_object mode is enabled.
The production provider now explicitly requests a valid JSON object. A regression
test verifies the message/response-format contract; the actual TKB request then
returned grounded user/world and assistant/suggestion experience facts.

Validation: provider tests 6 passed; full Python suite 456 passed, 25 skipped
(`.cache/pytest-json-live-fix`); Ruff passed. Runner records sanitized errors, TKB
response usage/model, upstream usage, effective extraction options, resolved
dependency versions and untracked source hashes. Run
`benchmark/memory-parity/runs/b2-extraction-20260909-1` is now executing 8 attribution/
time cases on both engines for 3 repetitions (48 records). This is extraction-only;
semantic review and operation/fault acceptance are still pending. No pass is
inferred from successful HTTP responses or the run's existence.

## Pi process and socket fault evidence (2.10 continuation)

Added `src/extensions/pi-agent/scripts/delivery-fault-smoke.mjs`. Independent Node
processes use built production TranscriptStore/ConversationDeliveryWorker and the
real MCP SDK HTTP client. A loopback MCP server fsyncs an isolated receiver ledger.
The service is stopped for a delivery attempt, restarted, and a new worker process
exits with code 86 immediately after remote acknowledgement. Another new process
replays the pending intent and persists local acceptance. Two wire deliveries yield
one receiver record; another restart sends nothing, and incomplete turns stay out.

`npm run build` passed. Both actual-network/process scenarios passed in
`.cache/delivery-process-fault-2/result.json` (not mocks or a simulated clock).
This tests Pi's process/socket boundary with a test receiver; the real PostgreSQL
queue/lease suite is separate, and combined end-to-end acceptance is not implied.
The 48-record extraction run is still live; first-repetition attribution and most
time outputs are available, but no final semantic score has been accepted.

## Semantic finding and review gate (2.10 continuation)

Baseline repetition 1 / parity-024 incorrectly classified the user's Hangzhou
trip as experience. The prompt now states that completed human actions are world,
reserves experience for the memory-owning Agent, and distinguishes actor from
message speaker. Extraction cache schema is bumped to v3. The live baseline
process had already imported the prior code and continues as pre-fix evidence.

`b2-extraction-actor-fix-20260909` completed 3 paired repetitions: two TKB outputs
completed, one returned HTTP 200 but degraded during local extraction parsing.
This failure is retained. Runner instrumentation now also records visible raw
model output (not reasoning text) to diagnose parse failures. A fresh three-repeat
parity-024 diagnostic run is active in `b2-extraction-month-diagnostic-20260909`.

Added report_extraction.py: it requires complete results and hash-bound explicit
reviews, rejects duplicate/changed evidence, applies the 90% / upstream-minus-5pp
threshold, and reports latency, failures and missing usage. Upstream total excludes
thoughts while TKB completion includes reasoning; normalization counts reasoning
once. Report gate tests: 2 passed. Full Python suite after actor prompt: 456 passed,
25 skipped. No semantic acceptance or B2 completion is claimed yet.

## Real lease expiry and reviewed baseline (2.10 continuation)

Added a real PostgreSQL test with an independent Python worker process. It claims
a one-second lease, pauses at an IPC boundary while real time expires, then resumes
after a replacement worker publishes and completes. Its late memory publication
raises RetentionLeaseLost and its late failure cannot replace completed status.
Revision stays at one. Targeted command: test_memory_scope_migration.py -k
expired_worker_process; 1 passed, 19 deselected (`.cache/pytest-process-lease-live`).

The month diagnostic completed all 6 paired outputs successfully. Each TKB output
uses world and preserves June 2025 in text/range; the earlier HTTP-200 parsing
degradation was not reproduced and remains in the actor-fix run as a failure.
Baseline repetitions 1–2 (32 records) now have explicit hash-bound reviews.
Reviews require every expected clause and preserve B2 actor/type invariants;
missing assistant clauses and user-travel experience misclassification are failed.
The baseline remains live. A fresh full 48-record post-fix run has started at
`benchmark/memory-parity/runs/b2-extraction-20260909-2` to check all cases for regressions.
Ruff passed. B2 acceptance and its batch commit remain pending.

## Production MCP/PostgreSQL replay and baseline report (2.10 continuation)

Added a full delivery boundary test: an independent Node/Pi process calls the
production Python MCP enqueue tool over HTTP, backed by the actual scoped
ConversationMemoryService and disposable PostgreSQL queue. After remote acceptance
the Pi process exits with code 86 before its local acknowledgement. Two fresh
processes recover/recheck it; the queue still contains exactly one source row with
the same document/operation IDs. The isolated test config initially missed
memory.enabled and was correctly rejected; the corrected config passed. Targeted
result: 1 passed, 20 deselected (`.cache/pytest-pi-mcp-postgres-4`).

Pi `npm run check`: security check, typecheck, 97 tests and build passed. The prior
20-case PostgreSQL/Neo4j suite passed (`.cache/pytest-b2-current-live`).

Pre-fix run 1 is terminal: all 48 rows and hash-bound reviews are present. Report
status is failed, not accepted. Under the fixed all-expected-clauses criterion,
TKB attribution is 100%, time is 75% (all three June-trip facts have wrong type).
Upstream scores 50% attribution (missing explicit assistant clauses) and 100% time.
These are extraction-only corpus scores, not overall engine rankings. Normalized
token totals including reasoning: upstream 121944, TKB 70114. p50/p95 seconds:
upstream 15.69/30.97; TKB 19.04/43.88. The post-fix run 2 remains active and is not
yet reviewed or accepted. No B2 batch-completion commit has been made.
Full 21-case PostgreSQL/Neo4j regression subsequently passed in 25.20 seconds
(`.cache/pytest-b2-e2e-full`), including the production MCP/Pi process replay.
Ruff, git diff checks and OpenSpec strict validation passed.
