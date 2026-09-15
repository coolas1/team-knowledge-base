## Purpose

Defines how the system selects, separates, and authorizes document evidence and conversation context so each source serves its intended purpose without silently displacing the other.

## ADDED Requirements

### Requirement: Query intent selects an evidence route
The system SHALL classify each agent query as knowledge, conversation-continuity, or mixed before automatic historical-memory recall or knowledge search. Knowledge queries SHALL use uploaded-document evidence as the authoritative primary pool and MAY retrieve a separately bounded conversation-context pool, continuity queries SHALL use conversation memory, and mixed queries SHALL execute the two routes independently.

#### Scenario: Knowledge question
- **WHEN** a user asks for information contained in indexed documents
- **THEN** the default search route retrieves the full requested uploaded-document quota and may also return a small independently ranked conversation-context group

#### Scenario: Conversation-continuity question
- **WHEN** a user explicitly asks about a prior preference, decision, commitment, or discussion
- **THEN** the system may retrieve relevant conversation memory without requiring a document search

#### Scenario: Ambiguous intent
- **WHEN** a query cannot be classified with sufficient confidence
- **THEN** the system SHALL choose the knowledge route, preserve document-first authority, and apply the configured auxiliary-memory cap

### Requirement: Mixed-source results remain separated
For a mixed query, the system SHALL return document evidence and conversation context in distinct groups with source type, authority, and provenance. Conversation context MUST NOT satisfy a requirement for document evidence or be emitted as a document citation.

#### Scenario: Mixed result
- **WHEN** a query needs both a policy document and a previously stated user preference
- **THEN** the response identifies the policy passage as document evidence and the preference as conversation context

### Requirement: Source quotas and identity caps are enforced
The system SHALL apply configurable per-source quotas and SHALL cap repeated candidates from the same document or conversation turn before final selection. A knowledge route SHALL preserve its full document-result quota and MAY add no more than the configured auxiliary-memory cap in a separate conversation-context group. Auxiliary memories MUST NOT displace document evidence or compete with it through raw cross-source scores.

#### Scenario: Conversation corpus is much larger
- **WHEN** conversation candidates greatly outnumber relevant document candidates for a knowledge query
- **THEN** conversation candidates occupy no document-evidence slots, no more than the auxiliary-memory cap are returned separately, and relevant documents retain their full quota

#### Scenario: One document produces many candidates
- **WHEN** one document yields multiple chunks, facts, or observations
- **THEN** the final result respects the configured per-document cap and preserves room for other relevant documents
