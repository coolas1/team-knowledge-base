## ADDED Requirements

### Requirement: Scoped document ownership
The system SHALL associate each document and its chunks with a trusted bank scope, preserving ownership across ingestion, editing, reindexing and background processing. Existing data and requests without scope SHALL use default-team; identical content in different scopes MUST NOT cause cross-scope reuse.

#### Scenario: Old client uploads a file
- **WHEN** an existing client uploads without scope fields
- **THEN** the document remains available in default-team with existing response fields

#### Scenario: Scoped document is reindexed
- **WHEN** a document in bank A is processed by a background task
- **THEN** its chunks, facts and graph projection remain in bank A

### Requirement: Scope enforcement on document and graph access
The system SHALL enforce trusted scope for document listing, detail, original content, editing, removal, ordinary GraphRAG retrieval and related graph/document information, including when memory query is disabled. An inaccessible document SHALL behave as nonexistent and MUST NOT expose content or metadata.

#### Scenario: Known document ID in another bank
- **WHEN** a caller in bank A requests or modifies a bank B document by ID
- **THEN** no document content or metadata is returned and no mutation occurs

#### Scenario: Ordinary search fallback
- **WHEN** a bank A request uses ordinary GraphRAG search with memory query disabled
- **THEN** chunks, related documents and graph descriptions contain only permitted source data
