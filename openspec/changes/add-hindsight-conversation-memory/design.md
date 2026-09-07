## Context

See `proposal.md` for motivation and `specs/conversation-memory/spec.md` for the behavior contract.

The embedded Hindsight implementation currently retains only indexed files. Every `MemoryUnit` has a non-null UUID foreign key to the existing `documents` table, and recall joins that table to recover titles and document provenance. Pi Agent separately persists session JSONL files and calls the engine only through MCP. Its public BFF API already supports session CRUD and streamed messages, but no lifecycle hook connects a completed turn to Hindsight.

The change must remain compatible with databases initialized by the current SQLAlchemy `create_all` flow. It must also keep memory operations out of the model-selected tool set, preserve the existing message and SSE shapes, and tolerate the engine or Hindsight providers being unavailable.

## Goals / Non-Goals

**Goals:**

- Reuse the embedded Hindsight retain/recall implementation and its existing `MemoryUnit.document_id` relationship.
- Keep conversation sources out of uploaded-document lists and GraphRAG file ingestion while retaining clear session/turn provenance.
- Make retention idempotent and durably queued so a slow extraction call does not delay streamed chat completion.
- Inject recalled memories for only the current model turn without persisting the injected block in Pi session history.
- Provide a separately invoked forget operation and operational diagnostics.

**Non-Goals:**

- User identity, tenant isolation, multiple banks, access-control policy, or private memory scopes.
- Replacing Pi Agent's JSONL session store with PostgreSQL.
- Importing the upstream Hindsight server, SDK, migrations, or control plane.
- Reprocessing historical Pi sessions automatically in the first release.
- Making conversation retention or recall directly selectable by the language model.

## Decisions

### 1. Use internal conversation Documents as storage anchors

Each completed turn will have one deterministic internal `Document` row with `file_type="conversation"`. A new additive `conversation_memory_sources` table will map that document UUID to `session_id`, `turn_id`, processing state, retry metadata, and timestamps. The transcript remains in `Document.raw_text`; speaker roles, source type, session ID, and turn ID are also copied into memory metadata during retain.

The document UUID will be UUIDv5 over a fixed project namespace plus `(session_id, turn_id)`. The mapping table also has a unique `(session_id, turn_id)` constraint. Both guarantees make enqueue retries idempotent.

Existing file-list, document-detail, and file-removal paths will exclude rows registered as conversation sources. Conversation rows bypass extraction, chunk creation, and the GraphRAG document pipeline; they exist only to satisfy the shared memory foreign key and lifecycle.

Alternatives considered:

- Generalizing `memory_units` to a polymorphic source FK is architecturally cleaner, but requires a destructive table migration and pervasive query changes that are disproportionate for the first conversation-memory release.
- Creating a parallel conversation memory schema duplicates the Hindsight repository and retrieval logic.
- Uploading transcripts through the existing file API pollutes the document UI and GraphRAG index and makes automatic lifecycle behavior difficult to distinguish from user uploads.

### 2. Extend retain with a structured source descriptor

The Hindsight retain facade will accept a structured input containing the existing document fields plus `source_type`, `context`, `tags`, and metadata. File hooks will populate the same defaults they use today. Conversation retention will use `source_type="conversation"`, a conversation-specific extraction context, and stable session/turn metadata.

Recall repository operations will accept an optional source filter. Automatic Pi recall will request only conversation sources so ordinary file evidence remains under the model's existing explicit search tools. Existing recall and query entry points will omit the filter and retain their current mixed knowledge behavior.

Alternative considered: retrieving all memories and filtering after ranking could cause relevant conversation memories to be displaced by file results before filtering, so filtering must occur in repository queries.

### 3. Use the source table as a durable retention queue

The internal retain transport will transactionally upsert the internal Document and its source row as `pending`, then return an accepted result. A background worker in the Python engine/webapp process will claim pending rows with a lease, call Hindsight retain, and mark them `completed` or retry with bounded backoff before `failed`.

This mirrors the existing durable Hindsight graph-outbox pattern. It keeps the Pi response path fast, survives process restarts, and exposes meaningful pending/failed counts through diagnostics. Only `completed` memory units participate in recall because replacement and completion occur in one repository transaction.

Alternatives considered:

- Awaiting fact extraction in Pi's message request adds model and embedding latency before the SSE completion event.
- Fire-and-forget tasks are fast but can silently lose completed turns during restarts.

### 4. Add private MCP memory operations

The engine MCP surface will add contract-validated operations for:

- recalling bounded conversation memory;
- enqueueing a completed conversation turn;
- forgetting all memory for a session;
- reporting conversation-memory queue health/status.

These operations will be called directly by the Pi runtime's MCP client and will not be included in the tools exposed to the language model. Existing public engine protocols remain valid; the conversation service is an optional adjacent capability so non-Hindsight engines can continue to run.

The Pi strict contract check will validate the internal operations when conversation memory is enabled. Deployment must therefore update the Python engine before enabling the updated Pi runtime.

Alternative considered: adding a second direct PostgreSQL or Hindsight HTTP client to Pi would duplicate configuration and let the Node process bypass the engine's ownership boundary.

### 5. Inject recall through a per-turn Pi extension hook

Pi's `before_agent_start` extension hook can replace the system prompt for only the current turn. A runtime-owned inline extension will use the current prompt to recall memories and append a bounded block to that turn's system prompt. It will not append a custom message or session entry.

The memory block will be clearly delimited as untrusted historical evidence. It will instruct the model to use relevant facts but never follow commands embedded in recalled content. Empty results produce no block. Recall has a short timeout and fails open with structured diagnostics.

Alternative considered: prefixing the user's prompt would persist recalled text as a user message and create a retention feedback loop.

### 6. Retain only successful visible turns

After `session.prompt()` completes, the runtime will identify the newly persisted user and assistant messages, derive a stable turn ID from their persisted entry/message identity, format only visible text with explicit roles, and enqueue the turn. Tool payloads, hidden reasoning, system prompts, injected memory, and incomplete assistant output are excluded.

Enqueue errors are caught and recorded before emitting the normal completion event; they do not replace a successful assistant answer with a message failure. Cancellation and model failure paths do not enqueue an assistant turn.

### 7. Separate session deletion from memory forgetting

The existing session DELETE endpoint keeps its current meaning and removes only the Pi JSONL session. A new explicit session-memory DELETE endpoint calls the engine forget operation. Forgetting marks pending work cancelled and deletes internal Documents for the session, relying on existing foreign-key cascades and Hindsight graph delete events to remove derived memory state while leaving file and other-session documents untouched.

This separation prevents an ordinary UI cleanup action from unexpectedly erasing knowledge that was intentionally made long lived.

### 8. Gate and bound the feature through configuration

Conversation memory will have explicit settings for enablement, recall result count, injected character/token budget, recall timeout, worker polling/lease/retry behavior, and retention context. Compose will enable it for the integrated deployment after the engine tools are available; standalone Pi deployments can leave it disabled.

Configuration validation will reject non-positive limits. Health output will report whether the feature is enabled and aggregate pending/failed state without returning memory content.

## Risks / Trade-offs

- [Shared scope can expose one teammate's retained conversation to another teammate] -> Document this accepted scope, label all results as shared team memory, and keep future bank/user fields additive.
- [Recalled user content can contain prompt-injection text] -> Inject it as explicitly untrusted evidence in a bounded system-prompt section and never expose memory operations to the model.
- [Conversation Documents could leak into file APIs] -> Centralize the internal-source exclusion predicate and add contract tests for list, detail, count, and removal paths.
- [Asynchronous retention creates eventual consistency] -> Return queue status, process quickly with a durable worker, and test that a completed source becomes recallable after processing.
- [LLM extraction cost grows with chat traffic] -> Retain one completed turn per job, bound transcript size, deduplicate by stable turn ID, and keep worker concurrency configurable.
- [Deleting a session and forgetting its memory can race with a worker] -> Cancel/lease-check source rows transactionally and make the worker verify current state before replacement.
- [Conversation facts can affect existing global reflect results] -> Tag and preserve source provenance; automatic recall filters to conversation sources while existing general queries intentionally continue to search the shared knowledge space.

## Migration Plan

1. Deploy the additive source table, queue repository, worker, and private MCP operations. Existing file memory remains untouched.
2. Verify engine MCP contract and worker health with conversation memory disabled in Pi.
3. Deploy the Pi runtime lifecycle integration and BFF forget proxy, then enable conversation memory in Compose.
4. Observe queue failure counts, retain latency, recall latency, and memory-source filtering before considering historical backfill.

Rollback disables Pi conversation memory and stops the worker. Existing conversation source rows and memories remain inert and can be explicitly forgotten or retained for a later re-enable; no file-memory rollback is required.
