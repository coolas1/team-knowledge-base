# B2 implementation progress

Tasks 2.1–2.8 implemented; overall 20/61. B2 acceptance task 2.10 is not complete.

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
