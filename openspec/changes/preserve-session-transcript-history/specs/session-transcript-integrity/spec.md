## Purpose

Ensure every accepted conversation message remains durably visible across model-context compaction, failures, cancellation, reconnection, process restart, and legacy-session recovery while keeping internal agent data private.

## ADDED Requirements

### Requirement: Visible transcript is independent from model context
The system SHALL maintain and return a user-visible transcript independently from the compacted context supplied to the language model. Context compaction MUST NOT remove, replace, or summarize previously recorded visible messages.

#### Scenario: Long session is compacted
- **WHEN** the model context for a session is compacted after multiple completed turns
- **THEN** retrieving that session SHALL return the same previously recorded user and assistant messages in their original order and text

#### Scenario: Internal agent entries exist
- **WHEN** a session contains system prompts, reasoning blocks, tool calls, tool results, memory-injection blocks, or compaction summaries
- **THEN** the visible transcript SHALL exclude those internal entries

#### Scenario: Session contains abandoned branches
- **WHEN** the underlying session history contains more than one branch
- **THEN** the visible transcript SHALL include only messages on the current active branch

### Requirement: Accepted user submissions are durable
The system SHALL durably record a valid user submission before acknowledging it as accepted or beginning model execution. An accepted user message MUST remain retrievable even if the model fails, the request is cancelled, the stream disconnects, or the Pi Agent process restarts.

#### Scenario: Submission is accepted
- **WHEN** a valid message request is durably recorded
- **THEN** the stream SHALL emit a `message.accepted` event containing stable session, turn, message, and client submission identifiers before model-generated events

#### Scenario: Model fails after acceptance
- **WHEN** model preflight or execution fails after a user submission is accepted
- **THEN** the transcript SHALL retain the user message and expose the turn as failed without exposing sensitive error details

#### Scenario: Request is cancelled or disconnected
- **WHEN** an accepted turn is cancelled explicitly or because its client disconnects
- **THEN** the transcript SHALL retain the user message and expose a cancelled or interrupted terminal state

#### Scenario: Process restarts during a turn
- **WHEN** the process restarts after acceptance but before a terminal outcome is recorded
- **THEN** the user message SHALL remain visible and the unfinished turn SHALL be recovered as interrupted rather than silently removed

### Requirement: Submission retries are idempotent
The message endpoint SHALL accept a client-generated submission identifier and SHALL associate at most one durable user message and one turn with the same identifier in a session.

#### Scenario: Client retries after losing the response
- **WHEN** a client repeats a message request with the same session and client submission identifier
- **THEN** the system SHALL return or stream the existing turn identity without appending or executing a duplicate turn

#### Scenario: Legacy client omits an identifier
- **WHEN** a valid existing client sends a message without a client submission identifier
- **THEN** the server SHALL generate one and process the request compatibly

### Requirement: Turn outcomes are represented consistently
The transcript SHALL use stable message and turn identifiers and SHALL represent accepted, running, completed, failed, cancelled, and interrupted outcomes without removing the associated user message.

#### Scenario: Turn completes
- **WHEN** the agent produces a final assistant answer
- **THEN** the system SHALL append the assistant message, mark the turn completed, and return both messages after reload

#### Scenario: Turn has no assistant answer
- **WHEN** an accepted turn reaches a failed, cancelled, or interrupted outcome without a final answer
- **THEN** the transcript SHALL return the user message and its terminal status without inventing an assistant message

### Requirement: Session summaries agree with transcript details
The visible message count reported for a session SHALL use the same visibility and active-branch rules as the session detail transcript.

#### Scenario: Session contains tool activity and compaction
- **WHEN** a session list entry is produced for a session containing internal tool events and compaction entries
- **THEN** its visible message count SHALL equal the number of messages returned by that session's detail endpoint

### Requirement: Existing sessions are recoverable without destructive migration
The system SHALL reconstruct the visible transcript of a legacy session from its complete active append-only branch when no adapter transcript journal exists. Recovery MUST preserve the original SDK session file.

#### Scenario: Legacy compacted session is opened
- **WHEN** an existing session has compaction entries and no transcript journal
- **THEN** all visible messages on its active branch SHALL be returned and a journal MAY be initialized without rewriting or deleting the SDK JSONL file

#### Scenario: Legacy session contains a malformed trailing record
- **WHEN** recovery encounters a malformed trailing record after valid durable entries
- **THEN** the system SHALL preserve all valid preceding transcript messages and report a sanitized degraded diagnostic

### Requirement: Browser state reconciles with durable history
The web client SHALL distinguish optimistic, accepted, and terminal message states and SHALL reconcile displayed messages by stable identifier with the durable session transcript after completion, failure, cancellation, reconnection, and refresh.

#### Scenario: Optimistic request is rejected before acceptance
- **WHEN** session creation or message submission fails before a `message.accepted` event
- **THEN** the client SHALL mark the optimistic message as unsaved and offer retry instead of presenting it as persisted

#### Scenario: Accepted stream disconnects
- **WHEN** the browser loses an SSE connection after `message.accepted`
- **THEN** reopening or refreshing the session SHALL recover the accepted message and its latest durable outcome without duplication

### Requirement: Conversation retention follows durable turns
Automatic conversation-memory retention SHALL derive completed user and assistant pairs from durable turn identities rather than positions in the compacted model-context array.

#### Scenario: Completion triggers context compaction
- **WHEN** a turn completes while model context compaction changes the in-memory message array
- **THEN** the completed durable user and assistant pair SHALL still be enqueued once for conversation-memory retention

#### Scenario: Incomplete turn is retained
- **WHEN** a turn is failed, cancelled, or interrupted
- **THEN** that incomplete turn SHALL NOT be enqueued as a completed conversation memory
