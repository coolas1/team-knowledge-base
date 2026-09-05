## Why

Pi Agent currently exposes its compaction-aware model context as visible session history. When a long conversation is compacted, older user messages remain in the append-only JSONL file but disappear from the history API and are removed from the UI after reload; requests that fail before the SDK persists its first assistant message can also leave an optimistic UI message without a durable record.

## What Changes

- Separate the user-visible session transcript from the context that Pi sends to the model.
- Reconstruct existing transcripts from the active JSONL branch so compaction never hides previously recorded user or assistant messages.
- Add an adapter-owned append-only turn journal that durably acknowledges each accepted user submission before model execution and records completed, failed, and cancelled outcomes.
- Give submissions and transcript messages stable identifiers, make retries idempotent, and expose an explicit `message.accepted` stream event.
- Reconcile the web UI from durable transcript state after completion, failure, cancellation, reconnect, or refresh instead of treating optimistic messages as persisted.
- Make session-list counts use the same visible transcript definition as session details.
- Derive conversation-memory retention from durable turn boundaries rather than indexes in the compaction-aware model message array.
- Recover existing sessions without destructive rewriting; continue filtering system prompts, model reasoning, tool payloads, and abandoned branches from visible history.

## Capabilities

### New Capabilities

- `session-transcript-integrity`: Durable, compaction-independent, idempotent session transcript storage, retrieval, streaming acknowledgement, recovery, and UI reconciliation.

### Modified Capabilities

None.

## Impact

- Pi Agent runtime and HTTP/SSE contract under `src/extensions/pi-agent`.
- Webapp agent proxy and React conversation UI under `src/frontend/webapp`.
- Persistent `piagentdata` volume gains adapter-owned transcript journal files alongside SDK session files.
- Existing session files remain compatible and become the recovery source when no journal exists.
- Conversation-memory enqueue logic changes its source of turn identity, without changing retained-memory content or public forget semantics.
