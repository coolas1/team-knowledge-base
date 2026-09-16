## MODIFIED Requirements

### Requirement: Search tools return only relevant evidence

Ordinary agent-facing knowledge search SHALL return current uploaded-document evidence by default and MAY return a separately bounded conversation-context group. The document pool SHALL retain the full requested quota; auxiliary conversation memory SHALL be independently retrieved, quality-gated, and capped so it cannot displace document evidence. Within each source pool, keyword-only candidates SHALL pass configurable salient-term coverage rules and candidates without lexical support SHALL pass the source-specific semantic floor. Heterogeneous source pools MUST NOT be ordered by directly comparing raw BM25 scores. When fewer than the requested number of sources pass, the response SHALL return fewer sources rather than padding with noise.

#### Scenario: Unrelated documents are filtered
- **WHEN** candidates match only one minor term of a multi-term query
- **THEN** those candidates are absent from the response

#### Scenario: Ordinary knowledge search encounters conversation matches
- **WHEN** relevant or high-scoring conversation memories exist for a knowledge search
- **THEN** they are excluded from document evidence and at most the configured number of relevant memories may appear in the distinct conversation-context group

#### Scenario: Relevant conversation memory is retained
- **WHEN** a caller explicitly selects a conversation or mixed route and a past conversation directly discusses the query
- **THEN** the relevant memory remains eligible in the distinct conversation-context group without becoming document evidence

#### Scenario: Explicit mixed search
- **WHEN** a caller requests both document and conversation sources
- **THEN** each source is gated and ranked within its own pool before source-aware fusion, and the response preserves their distinct groups

#### Scenario: Few sources pass the gate
- **WHEN** only three document candidates pass although more were requested
- **THEN** the response contains three sources and reports filtering and duplicate-collapse counts

### Requirement: Documents remain findable by metadata when body text is noisy

The system SHALL maintain a bounded clean document retrieval representation using title, filename, overview, tags, and entities, independently from original chunk text. A document whose extracted body is noisy or whose relevant topic is implicit in a local chunk SHALL remain discoverable through the document representation, while user-visible evidence and reading views SHALL use original extracted text.

#### Scenario: Scanned paper found by its title
- **WHEN** a query matches a document title or overview and extracted body text is mostly unreadable noise
- **THEN** the document appears among the returned sources with an original-text passage or an explicit indication that no reliable passage is available

#### Scenario: Reading view is unchanged
- **WHEN** a user opens the document or the agent reads document evidence
- **THEN** the displayed text is the original extraction, not the retrieval representation

## ADDED Requirements

### Requirement: Duplicate representations collapse before selection
Equivalent source chunks, extracted facts, observations, and repeated conversation turns SHALL collapse by document, turn, derivation, and semantic identity before final selection. Collapse SHALL retain the strongest provenance-bearing representative and SHALL be reported in trace.

#### Scenario: One passage has three derived representations
- **WHEN** a source chunk, extracted fact, and observation express the same evidence
- **THEN** they consume at most one configured evidence slot unless distinct representations are explicitly requested

### Requirement: Knowledge answers remain document grounded
Material claims presented as knowledge-base answers SHALL be supported by returned uploaded-document evidence and SHALL cite its title and document identifier. Conversation context alone MUST NOT be presented as knowledge-base evidence.

#### Scenario: Only conversation context is found
- **WHEN** a knowledge query has relevant conversation memories but no sufficient document evidence
- **THEN** the answer states that the knowledge base lacks sufficient document evidence and does not cite the conversation as a document
