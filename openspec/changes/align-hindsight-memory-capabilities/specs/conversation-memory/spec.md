## MODIFIED Requirements

### Requirement: Shared team memory scope
Existing conversation requests SHALL default to the existing shared team scope without requiring user, bank or tenant fields. Explicitly configured sessions SHALL use a server-validated memory scope consistently for retention and retrieval. Historical shared memories SHALL remain shared unless explicitly migrated; the system MUST NOT infer private ownership from message text.

#### Scenario: Existing client sends a message
- **WHEN** a client uses the existing session message API without identity or bank fields
- **THEN** automatic recall and retention operate in the shared team memory scope

#### Scenario: Explicit isolated session
- **WHEN** a session is configured with an allowed isolated scope
- **THEN** its retention and retrieval use that scope without reading other scopes implicitly

### Requirement: Bounded and non-persistent memory injection
The system SHALL bound recalled memory context by configured retrieval and size limits. Injected memory MUST be treated as untrusted historical evidence, MUST NOT override trusted instructions, and MUST NOT be added to visible history or retained as newly stated content. Configurable injection SHALL support type and source-time labels while preserving provenance.

#### Scenario: Recalled context is injected
- **WHEN** automatic recall returns one or more memories
- **THEN** the agent receives a bounded untrusted evidence block absent from visible history

#### Scenario: A recalled fact contributes to an answer
- **WHEN** the assistant uses a recalled fact in its response
- **THEN** retention excludes the injected block and retains only the visible user and assistant turn

### Requirement: Explicit conversation forgetting
The system SHALL provide an explicit operation to remove memories produced by a specified conversation session. Forgetting SHALL preserve unrelated source facts and invalidate or recompute derived observations and mental models that depend on removed facts. In-flight retention and refresh MUST NOT resurrect forgotten content.

#### Scenario: Session history is deleted normally
- **WHEN** a client deletes a Pi Agent session through the existing session deletion operation
- **THEN** session history is deleted while previously retained long-term memories remain available

#### Scenario: Conversation memory is explicitly forgotten
- **WHEN** a client invokes the explicit forget operation for a session
- **THEN** its source memories are removed, affected derived content stops being returned as valid evidence, and unrelated source memories remain intact

## ADDED Requirements

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
