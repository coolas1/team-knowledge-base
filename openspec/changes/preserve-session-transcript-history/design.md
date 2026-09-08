## Context

See `proposal.md` for motivation. Pi SDK 0.83.0 stores append-only tree entries in session JSONL files, but `AgentSession.messages` contains the compaction-aware context sent to the model. The current detail endpoint projects visible history from that context, while the list endpoint reports every SDK `message` entry, including tool activity. The browser then replaces its local list with the incomplete detail response.

The SDK also delays creation of a new session file until an assistant entry exists. This is useful for avoiding empty CLI sessions, but it does not provide the durable acceptance boundary required by a web message API. Pi Agent runs as one process against a persistent named volume and already enforces one active turn per session.

## Goals / Non-Goals

**Goals:**

- Restore all visible messages from the active branch of every existing session.
- Establish a durable boundary before the server reports that a user submission was accepted.
- Keep visible transcript history independent from model-context compaction and SDK implementation details.
- Preserve backward compatibility for clients that only understand `role` and `text` or omit a client submission identifier.
- Reuse durable turn identity for conversation-memory retention and operational diagnostics.

**Non-Goals:**

- Showing system prompts, reasoning, tool payloads, recalled memory blocks, or abandoned branches.
- Removing or changing model-context compaction.
- Supporting concurrent Pi Agent replicas writing to the same session volume.
- Replacing the existing SDK session files as the source of model context.
- Retaining partial assistant deltas as transcript messages.

## Decisions

### Maintain an adapter-owned append-only transcript journal

Each session receives a versioned journal below the existing Pi Agent data directory. The journal contains a header plus immutable events for user acceptance, assistant completion, and terminal turn status. Appends use an exclusive per-session queue, append mode, and an explicit file sync before `message.accepted` is emitted.

The accepted event records `sessionId`, `turnId`, `messageId`, `clientMessageId`, timestamp, and exact user text. A completed event records the assistant message and final answer. Failed, cancelled, and interrupted events record a bounded public error code and status without stack traces, credentials, prompts, or tool payloads.

This journal becomes the visible-transcript source of truth once it exists. The SDK JSONL remains the model-context source of truth.

Alternatives considered:

- Returning `sessionManager.getBranch()` alone fixes compaction-hidden history but leaves submissions vulnerable before the SDK writes its first assistant entry.
- Disabling compaction would eventually exceed model context and would not close the initial persistence window.
- Storing transcripts in Postgres would introduce a database dependency into the isolated Pi Agent service and complicate local operation. The existing persistent volume already provides the required durability boundary.
- Patching `node_modules` persistence would bind product history guarantees to an external package implementation and would be lost on reinstall.

### Recover legacy history from the active SDK branch

When no journal exists, the adapter opens the SDK session and walks `sessionManager.getBranch()` from the current leaf. It projects only `message` entries with non-empty user or assistant text. Compaction entries remain traversal nodes but never replace earlier visible messages. Tool-only assistant entries, tool results, system/custom messages, and reasoning-only blocks are omitted.

Recovery initializes a journal under the session lock using a temporary file, sync, and atomic rename. It never rewrites the SDK JSONL. Message and turn identifiers are derived deterministically from existing SDK entry IDs so repeated recovery cannot duplicate history. A malformed trailing journal record is treated as a torn append: valid preceding events remain available, the turn is marked degraded, and future writes continue only after a safe repair or journal rebuild from the SDK source.

Using all SDK entries instead of the active branch was rejected because branched sessions could expose abandoned conversation paths.

### Treat message acceptance as an explicit protocol event

The message request accepts optional `clientMessageId`; old clients receive a generated identifier. Before invoking `session.prompt`, the runtime reserves an idempotency key and syncs the accepted user event. It then emits `message.accepted` before assistant, tool, citation, limit, completion, or failure events.

A repeated `(sessionId, clientMessageId)` returns the existing identity and state. It does not invoke the model again. If the existing turn is still active, the request observes its durable state rather than starting concurrent execution. The existing one-active-turn guard remains authoritative for different submission IDs.

All additions to request and response shapes are optional for old clients. Existing SSE event names retain their fields.

### Represent turn lifecycle in the transcript projection

The projection folds immutable journal events into ordered transcript messages and turn state:

```text
accepted --> running --> completed
    |           |------> failed
    |           |------> cancelled
    |           `------> interrupted (recovered after restart)
    `------------------> failed before model start
```

Only completed turns have a durable assistant message. A startup recovery pass converts accepted/running turns with no live owner to interrupted. Retrying uses a new client submission ID while retaining the interrupted historical turn; replaying the same ID remains idempotent.

Session detail keeps `role` and `text` and adds stable IDs, timestamp, turn ID, and status. `messageCount` is defined as the count of visible transcript messages. Internal event counts are not presented as conversation records.

### Reconcile the browser by identifiers

The browser creates a client submission ID and an optimistic user row. Receipt of `message.accepted` replaces its temporary identity with durable IDs. Terminal SSE events update the same turn. If submission fails before acceptance, the row is marked unsaved and can be retried. If an accepted stream ends unexpectedly, the client fetches session detail and merges by durable message ID.

Session switching and initial loading replace local state only with the durable transcript. Completion, failure, cancellation, and reconnect all perform or schedule reconciliation so partial network delivery cannot remove accepted content.

### Drive conversation-memory retention from the completed journal event

The completed turn event contains the stable user/assistant pair and turn ID needed by the existing enqueue API. Retention runs after the completed event is synced and remains failure-isolated from chat. Its idempotency key is the durable turn ID. This removes the current dependence on array slicing across compaction.

## Risks / Trade-offs

- [Two append-only files describe different views of a session] -> Give them explicit ownership: SDK JSONL for model context and adapter journal for visible transcript; validate both in recovery tests.
- [Crash occurs between SDK completion and journal completion] -> Preserve the accepted user turn as interrupted and allow retry; optionally reconcile a matching final SDK assistant entry during startup only when identity is unambiguous.
- [Journal append is torn by process or host failure] -> Use one JSON event per line, sync acknowledged events, accept valid prefix records, and expose a sanitized degraded diagnostic.
- [Existing sessions are large] -> Recover lazily per session, cache projections for loaded sessions, and avoid loading tool payload bodies into API output.
- [Idempotency records grow with transcript history] -> Rebuild the lookup from compact event metadata when opening a journal; no separate mutable index is required initially.
- [Client disconnect races with completion] -> Treat durable terminal state as authoritative and make browser reconciliation mandatory after an unexpected stream end.

## Migration Plan

1. Deploy transcript reading and legacy active-branch projection while continuing to serve existing request fields.
2. Enable lazy, atomic journal initialization for existing sessions and verify recovered visible counts against active SDK branch counts.
3. Enable durable acceptance, idempotency, lifecycle events, and journal-based conversation retention.
4. Deploy the browser client that sends client submission IDs and reconciles by durable IDs.
5. Audit the existing persistent volume. The observed compacted sessions should regain their hidden user messages without modifying SDK JSONL files.
6. Monitor journal recovery failures, interrupted turns, duplicate submission attempts, and list/detail count mismatches.

Rollback disables journal writes and returns to active-branch projection from the untouched SDK JSONL files. Journal files remain append-only and can be ignored by the previous runtime. The original session data is never rewritten, so rollback requires no data restoration.
