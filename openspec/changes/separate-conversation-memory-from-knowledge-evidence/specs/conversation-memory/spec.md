## MODIFIED Requirements

### Requirement: Automatic pre-response recall
The system SHALL automatically retrieve conversation memories before response generation only when the query is classified as conversation-continuity or mixed with sufficient confidence. Retrieval SHALL use the current message and bounded recent visible context for disambiguation, SHALL apply conversation-specific relevance and freshness gates, and SHALL omit automatic memory recall for knowledge queries and low-confidence classifications.

#### Scenario: Relevant memory is available
- **WHEN** a user explicitly continues a prior preference, decision, commitment, or discussion and a relevant retained memory passes the gates
- **THEN** the system supplies the bounded retained context before response generation

#### Scenario: Knowledge query is asked
- **WHEN** a user asks for information from indexed documents
- **THEN** the system does not automatically inject conversation memory

#### Scenario: No relevant memory is available
- **WHEN** routing confidence or memory relevance is below its configured threshold
- **THEN** the system generates the response without conversation-memory injection

### Requirement: Bounded and non-persistent memory injection
The system SHALL bound recalled memory context by configured result and character limits that are independent of document-evidence budgets. Injected memory MUST be labeled as non-authoritative historical context, MUST preserve source/session/turn provenance, MUST NOT appear as a document citation, and MUST NOT be added to visible history or retained again as newly stated content.

#### Scenario: Recalled context is injected
- **WHEN** automatic recall returns one or more eligible memories
- **THEN** the agent receives a bounded, provenance-labeled context block separate from document evidence and absent from visible history

#### Scenario: A recalled fact contributes to an answer
- **WHEN** the assistant uses recalled context in its response
- **THEN** retention excludes the injected block and does not create an authoritative duplicate of that context

### Requirement: Automatic post-response retention
The system SHALL evaluate each successfully completed visible user/assistant turn for durable-memory eligibility without requiring a model tool call. It SHALL retain user-originated durable facts, preferences, decisions, commitments, and state with stable provenance, but MUST NOT retain ordinary assistant summaries, tool results, document excerpts, transient discussion, or unsupported generated claims as authoritative conversation memory. Cancelled or failed turns MUST NOT be retained.

#### Scenario: Successful response completes
- **WHEN** a completed turn contains a stable user fact, preference, decision, commitment, or state that passes the retention policy
- **THEN** the eligible content is retained with speaker, session, turn, policy, and source-time provenance while ineligible turn content is omitted

#### Scenario: Assistant summarizes a document
- **WHEN** the assistant answer consists of facts or excerpts obtained from knowledge tools
- **THEN** that answer is not retained as independent authoritative conversation memory

#### Scenario: Response is cancelled or fails
- **WHEN** response generation is cancelled or terminates with an error before completion
- **THEN** the system does not retain an incomplete assistant turn

## ADDED Requirements

### Requirement: Conversation memories support lifecycle quality controls
Conversation memories SHALL support semantic duplicate collapse, explicit confirmation state, supersession, expiry or time decay where applicable, and source-quality weighting. The system SHALL retain audit provenance for retired records without returning them in ordinary recall.

#### Scenario: Preference changes
- **WHEN** a user clearly replaces an earlier preference
- **THEN** the new memory supersedes the old memory and ordinary recall returns the current preference

#### Scenario: Same fact is repeated
- **WHEN** equivalent content is retained from multiple turns
- **THEN** ordinary recall returns one current representative rather than multiple duplicate results

### Requirement: Allowed derived memories preserve derivation provenance
If policy explicitly permits an assistant-originated decision or commitment to be retained, the memory SHALL identify its origin and SHALL preserve any document and memory evidence identifiers from which it was derived. A derived record without valid evidence MUST NOT become authoritative.

#### Scenario: Assistant records an agreed plan
- **WHEN** the user confirms an assistant-proposed plan grounded in documents
- **THEN** the retained decision records the confirming turn and the supporting evidence identifiers
