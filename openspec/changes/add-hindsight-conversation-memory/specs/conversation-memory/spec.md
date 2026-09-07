## Purpose

Provide durable, automatic memory across Pi Agent conversations while preserving existing chat behavior and keeping conversation-derived knowledge distinguishable from indexed file knowledge.

## ADDED Requirements

### Requirement: Automatic pre-response recall
The system SHALL automatically retrieve conversation memories relevant to the current user message before generating an assistant response. Retrieval SHALL use the current message and MAY use recent visible conversation context to disambiguate the query.

#### Scenario: Relevant memory is available
- **WHEN** a user sends a message related to facts retained from an earlier conversation
- **THEN** the system supplies the relevant retained facts to the agent before response generation

#### Scenario: No relevant memory is available
- **WHEN** a user sends a message for which no conversation memory is relevant
- **THEN** the system generates the response without adding an empty or fabricated memory context

### Requirement: Bounded and non-persistent memory injection
The system SHALL bound recalled memory context by configured retrieval and size limits. Injected memory context MUST be treated as auxiliary instructions and MUST NOT be added to the visible user/assistant message history or retained again as if it were newly stated conversation content.

#### Scenario: Recalled context is injected
- **WHEN** automatic recall returns one or more memories
- **THEN** the agent receives a bounded memory block that is absent from the session's visible message history

#### Scenario: A recalled fact contributes to an answer
- **WHEN** the assistant uses a recalled fact in its response
- **THEN** the next retention operation excludes the injected memory block itself and retains only the visible user and assistant turn

### Requirement: Automatic post-response retention
The system SHALL retain each successfully completed user/assistant turn without requiring the user or model to call a memory tool. Retained content SHALL preserve speaker roles and SHALL record stable session and turn provenance.

#### Scenario: Successful response completes
- **WHEN** the agent successfully completes a response to a user message
- **THEN** the system submits that user/assistant turn for durable conversation-memory retention

#### Scenario: Response is cancelled or fails
- **WHEN** response generation is cancelled or terminates with an error before completion
- **THEN** the system does not retain an incomplete assistant turn

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
All conversation memories introduced by this change SHALL use the existing shared team knowledge scope. The system SHALL NOT require a user identity, per-user bank, or tenant identifier in existing session or message requests.

#### Scenario: Existing client sends a message
- **WHEN** a client uses the existing session message API without identity or bank fields
- **THEN** automatic recall and retention operate in the shared team memory scope

### Requirement: Failure-isolated memory lifecycle
Recall and retention failures SHALL NOT prevent an otherwise valid conversation from continuing. The system SHALL expose failures through structured logs and health or runtime diagnostics without returning sensitive retained content.

#### Scenario: Recall service is unavailable
- **WHEN** automatic recall fails or exceeds its configured timeout
- **THEN** response generation continues without recalled context and the failure is recorded diagnostically

#### Scenario: Retention service is unavailable
- **WHEN** a completed turn cannot be retained
- **THEN** the completed answer remains available to the client and the retention failure is recorded diagnostically

### Requirement: Explicit conversation forgetting
The system SHALL provide an explicit operation to remove memories produced by a specified conversation session. Forgetting a conversation SHALL NOT delete indexed file memories or memories attributed to other sessions.

#### Scenario: Session history is deleted normally
- **WHEN** a client deletes a Pi Agent session through the existing session deletion operation
- **THEN** the session history is deleted while previously retained long-term memories remain available

#### Scenario: Conversation memory is explicitly forgotten
- **WHEN** a client invokes the explicit forget operation for a session
- **THEN** memories attributed to that session are removed and unrelated file and conversation memories remain intact

### Requirement: Existing API compatibility
Existing session creation, listing, detail, message streaming, cancellation, and deletion requests SHALL remain valid without new required fields. Existing knowledge recall and file ingestion responses SHALL remain backward compatible.

#### Scenario: Existing web client is used unchanged
- **WHEN** the current web client creates a session and streams a message
- **THEN** the request succeeds with the existing request and event shapes while automatic memory runs transparently

#### Scenario: Existing knowledge client queries files
- **WHEN** an existing client calls the current recall or query interfaces
- **THEN** all previously documented fields and semantics remain available
