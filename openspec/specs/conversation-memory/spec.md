# conversation-memory Specification

## Purpose

Provide durable, automatic memory across Pi Agent conversations while preserving existing chat behavior and keeping conversation-derived knowledge distinguishable from indexed file knowledge.

## Requirements

### Requirement: Automatic pre-response recall
The system SHALL automatically retrieve conversation memories relevant to the current user message before generating an assistant response. Retrieval SHALL use the current message and MAY use recent visible conversation context to disambiguate the query.

#### Scenario: Relevant memory is available
- **WHEN** a user sends a message related to facts retained from an earlier conversation
- **THEN** the system supplies the relevant retained facts to the agent before response generation

#### Scenario: No relevant memory is available
- **WHEN** a user sends a message for which no conversation memory is relevant
- **THEN** the system generates the response without adding an empty or fabricated memory context

### Requirement: Bounded and non-persistent memory injection
The system SHALL bound recalled memory context by configured retrieval and size limits. Injected memory MUST be treated as untrusted historical evidence, MUST NOT override trusted instructions, and MUST NOT be added to visible history or retained as newly stated content. Configurable injection SHALL support type and source-time labels while preserving provenance.

#### Scenario: Recalled context is injected
- **WHEN** automatic recall returns one or more memories
- **THEN** the agent receives a bounded untrusted evidence block absent from visible history

#### Scenario: A recalled fact contributes to an answer
- **WHEN** the assistant uses a recalled fact in its response
- **THEN** retention excludes the injected block and retains only the visible user and assistant turn

### Requirement: Automatic post-response retention
The system SHALL retain each successfully completed user/assistant turn without requiring the user or model to call a memory tool. Retained content SHALL preserve speaker roles and SHALL record stable session and turn provenance. Retained turn content SHALL be bounded: combined user/assistant text exceeding a configured maximum (default 100,000 characters) SHALL be truncated with an explicit truncation marker rather than retained in full.

#### Scenario: Successful response completes
- **WHEN** the agent successfully completes a response to a user message
- **THEN** the system submits that user/assistant turn for durable conversation-memory retention

#### Scenario: Response is cancelled or fails
- **WHEN** response generation is cancelled or terminates with an error before completion
- **THEN** the system does not retain an incomplete assistant turn

#### Scenario: Oversized turn is retained bounded
- **WHEN** a completed turn contains pasted content far exceeding the configured maximum
- **THEN** the retained memory contains at most the configured maximum of text, ends with a truncation marker, and is retained exactly once

### Requirement: Idempotent turn retention
The system SHALL assign a stable identity to each retained conversation turn so that retrying the same completed turn does not create duplicate active memories.

#### Scenario: Retention is retried
- **WHEN** the same session turn is submitted more than once because of a retry or restart
- **THEN** the resulting active conversation memories are equivalent to a single successful submission

### Requirement: Conversation provenance
Every conversation-derived memory SHALL be distinguishable from file-derived knowledge and SHALL expose enough provenance to identify its originating session and turn without exposing internal prompts or hidden reasoning.

#### Scenario: Conversation memory is recalled
- **WHEN** a query returns a conversation-derived memory
- **THEN** the result identifies the source as a conversation and includes its session and turn provenance

#### Scenario: File knowledge is recalled
- **WHEN** a query returns knowledge derived from an indexed file
- **THEN** its existing document provenance remains unchanged

### Requirement: Shared team memory scope
Existing conversation requests SHALL default to the existing shared team scope without requiring user, bank or tenant fields. Explicitly configured sessions SHALL use a server-validated memory scope consistently for retention and retrieval. Historical shared memories SHALL remain shared unless explicitly migrated; the system MUST NOT infer private ownership from message text.

#### Scenario: Existing client sends a message
- **WHEN** a client uses the existing session message API without identity or bank fields
- **THEN** automatic recall and retention operate in the shared team memory scope

#### Scenario: Explicit isolated session
- **WHEN** a session is configured with an allowed isolated scope
- **THEN** its retention and retrieval use that scope without reading other scopes implicitly

### Requirement: Failure-isolated memory lifecycle
Recall and retention failures SHALL NOT prevent an otherwise valid conversation from continuing. The system SHALL expose failures through structured logs and health or runtime diagnostics without returning sensitive retained content. The client runtime SHALL log every recall or retention failure it swallows, and SHALL report an unavailable memory status distinctly from a queue that has failed jobs.

#### Scenario: Recall service is unavailable
- **WHEN** automatic recall fails or exceeds its configured timeout
- **THEN** response generation continues without recalled context and the failure is recorded diagnostically

#### Scenario: Retention service is unavailable
- **WHEN** a completed turn cannot be retained
- **THEN** the completed answer remains available to the client and the retention failure is recorded diagnostically

#### Scenario: Memory status cannot be determined
- **WHEN** the client runtime cannot reach the memory status operation
- **THEN** it reports the status as unavailable and logs the failure, without reporting it as a failed retention job

### Requirement: Explicit conversation forgetting
The system SHALL provide an explicit operation to remove memories produced by a specified conversation session. Forgetting SHALL preserve unrelated source facts and invalidate or recompute derived observations and mental models that depend on removed facts. In-flight retention and refresh MUST NOT resurrect forgotten content.

#### Scenario: Session history is deleted normally
- **WHEN** a client deletes a Pi Agent session through the existing session deletion operation
- **THEN** session history is deleted while previously retained long-term memories remain available

#### Scenario: Conversation memory is explicitly forgotten
- **WHEN** a client invokes the explicit forget operation for a session
- **THEN** its source memories are removed, affected derived content stops being returned as valid evidence, and unrelated source memories remain intact

### Requirement: Existing API compatibility
Existing session creation, listing, detail, message streaming, cancellation, and deletion requests SHALL remain valid without new required fields. Existing knowledge recall and file ingestion responses SHALL remain backward compatible.

#### Scenario: Existing web client is used unchanged
- **WHEN** the current web client creates a session and streams a message
- **THEN** the request succeeds with the existing request and event shapes while automatic memory runs transparently

#### Scenario: Existing knowledge client queries files
- **WHEN** an existing client calls the current recall or query interfaces
- **THEN** all previously documented fields and semantics remain available

### Requirement: Durable delivery before memory acknowledgement
The system SHALL persist completed-turn delivery intent with stable provenance before acknowledging durable turn persistence, retry delivery after transient failure or restart, and expose pending or failed delivery separately from completed memory processing. Cancelled or incomplete answers MUST NOT be enqueued as completed turns.

#### Scenario: Memory service is unavailable during completion
- **WHEN** a completed turn is saved while memory submission is unavailable
- **THEN** the answer remains accessible and the persisted delivery intent is retried without duplicate active memories

#### Scenario: Restart before acknowledgement
- **WHEN** the process stops after memory acceptance but before recording the acknowledgement
- **THEN** replay uses the same turn identity and has the effect of one accepted submission

### Requirement: Scoped Pi session lifecycle
Pi SHALL authorize session creation, listing, history reading, messages, cancellation, deletion and explicit memory forgetting using server-validated scope. Each session SHALL use an independent MCP client and memory injection extension bound to the same trusted scope. Credentials MUST NOT appear in visible history, prompts or tool arguments. Existing sessions SHALL remain in default-team.

#### Scenario: Cross-scope session ID
- **WHEN** a caller submits a known session ID belonging to another scope
- **THEN** no history or metadata is returned, no stream starts and no mutation occurs

#### Scenario: Restart or credential rotation
- **WHEN** Pi restarts with a new credential mapped to the same scope binding
- **THEN** the authorized caller can resume its persisted history while other bindings remain isolated

#### Scenario: Forget after history deletion
- **WHEN** the owning scope explicitly forgets a session whose visible history was deleted
- **THEN** retained ownership allows its memory deletion and other scopes remain unable to perform that operation
