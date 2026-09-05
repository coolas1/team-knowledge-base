## 1. Transcript journal foundation

- [x] 1.1 Define versioned transcript header, user-accepted, assistant-completed, and turn-terminal event types plus the public message/turn projection; verify TypeScript exhaustiveness tests cover every event and lifecycle state.
- [x] 1.2 Add `PI_AGENT_TRANSCRIPT_DIR` configuration defaulting below `PI_AGENT_DATA_DIR`, validate that its resolved path is usable, and verify default, override, and invalid configuration tests.
- [x] 1.3 Implement a per-session append-only transcript store with serialized writers, append mode, bounded record size, and file sync before acknowledgement; verify a test can reopen the file immediately after acknowledgement and read the accepted message.
- [x] 1.4 Implement journal folding into ordered visible messages, turn states, and an idempotency lookup; verify completed, failed, cancelled, interrupted, and repeated-event fixtures produce deterministic projections.
- [x] 1.5 Handle a malformed or torn trailing journal record by preserving its valid prefix and returning a sanitized degraded diagnostic; verify corruption tests expose no transcript text, paths, stack traces, or secrets in diagnostics.

## 2. Existing-session recovery

- [x] 2.1 Implement legacy transcript projection from the SDK session manager's active branch, selecting only non-empty user and assistant text and stable entry metadata; verify fixtures exclude tool, reasoning, system, custom, memory, and compaction entries.
- [x] 2.2 Verify active-branch traversal excludes abandoned branches while retaining messages that precede compaction entries; use a branched, multiply compacted real `SessionManager` fixture.
- [x] 2.3 Add lazy journal initialization under the session write lock using temporary-file sync and atomic rename, with deterministic IDs derived from SDK entries; verify repeated and concurrent recovery is idempotent and does not modify the SDK JSONL file.
- [x] 2.4 Make session detail and session-list counts use the same visible transcript projection; verify sessions containing tool calls and compaction report `messageCount === messages.length`.
- [x] 2.5 Add a read-only audit command for comparing SDK active-branch counts, journal counts, compaction counts, and recovery errors without printing message bodies; verify it detects an intentionally incomplete fixture.

## 3. Durable message execution

- [x] 3.1 Extend the runtime message input with an optional client submission ID and generate a validated ID for legacy callers; verify invalid IDs are rejected and omitted IDs remain backward compatible.
- [x] 3.2 Before invoking `session.prompt`, create stable turn/user-message IDs, sync the accepted journal event, and emit `message.accepted`; verify event-order tests prove acceptance precedes model, tool, citation, completion, and failure events.
- [x] 3.3 Enforce `(sessionId, clientMessageId)` idempotency so a repeated request returns the existing identity and state without appending a duplicate or invoking the model again; verify retries during and after a turn execute exactly once.
- [x] 3.4 Append running and completed events around successful model execution, persist the final assistant answer once, and return stable IDs and timestamps in session detail; verify reload and process-recreation tests return the same transcript.
- [x] 3.5 Persist sanitized failed and cancelled terminal events after accepted turns, including disconnect-driven cancellation; verify accepted user messages survive model preflight failure, provider failure, explicit cancellation, and SSE disconnect.
- [x] 3.6 On startup or first session load, mark accepted/running turns without a live owner as interrupted; verify a simulated crash after acceptance retains the user message and permits retry with a new client ID.
- [x] 3.7 Emit structured operational logs for session ID, turn ID, lifecycle status, recovery result, and idempotent replay while excluding message bodies and internal errors; verify log-capture tests enforce the allowed field set.

## 4. Conversation-memory retention

- [x] 4.1 Replace compaction-sensitive message-array slicing with the durable completed-turn event as the source of user text, assistant text, and turn ID; verify a turn that triggers compaction is still enqueued once.
- [x] 4.2 Keep failed, cancelled, interrupted, and assistant-empty turns out of conversation retention and preserve failure isolation from chat; verify lifecycle matrix tests and existing forget-memory contract tests.

## 5. HTTP, proxy, and browser reconciliation

- [x] 5.1 Extend the Pi HTTP/SSE contract with optional `clientMessageId`, additive transcript metadata, lifecycle status, and `message.accepted` while retaining existing fields; verify old request bodies and old SSE consumers still pass contract tests.
- [x] 5.2 Propagate the extended request and SSE stream unchanged through the FastAPI BFF, including disconnect cancellation; verify proxy tests cover accepted, completed, failed, cancelled, and abruptly closed streams.
- [x] 5.3 Generate a client submission ID for each browser send and model optimistic, accepted, completed, failed, cancelled, and interrupted rows; verify reducer tests preserve one row per stable ID across repeated events.
- [x] 5.4 Reconcile session detail by message ID after completion, failure, cancellation, and unexpected stream end; verify reconnect and refresh tests recover accepted messages without duplication or disappearance.
- [x] 5.5 Mark requests rejected before `message.accepted` as unsaved and provide a retry action with a new submission ID; verify UI tests distinguish unsaved messages from accepted failed turns.

## 6. Deployment and acceptance

- [x] 6.1 Update `.env.example`, Compose, and Pi Agent documentation with transcript directory ownership, durability semantics, API additions, recovery behavior, monitoring fields, and rollback steps; verify `docker compose config --quiet` succeeds.
- [x] 6.2 Run the recovery audit against the existing `piagentdata` volume, confirm compacted sessions regain all active-branch visible messages and zero source JSONL files change, and record count-only evidence without conversation content.
- [x] 6.3 Run end-to-end fault injection for compaction, provider preflight failure, provider execution failure, cancellation, disconnect, duplicate retry, torn append, and process restart; verify every accepted user message remains retrievable with the specified lifecycle state.
- [x] 6.4 Run Pi Agent security gate, typecheck, unit tests and build, web client tests and build, affected Python proxy tests, formatting/lint checks, and `openspec validate preserve-session-transcript-history --strict`; fix all failures and record the final validation results.
