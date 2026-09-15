## ADDED Requirements

### Requirement: Retrieval migrations use a gated rollout
The deployment SHALL roll out document retrieval representations, lexical indexes, and conversation-memory cleanup through resumable, scope-bounded stages: schema preparation, dual write, backfill or cleanup dry-run, completeness validation, read switch, and post-switch verification. It MUST NOT enable indexed or hierarchical reads for a scope whose required backfill is incomplete.

#### Scenario: Backfill is incomplete
- **WHEN** completeness validation finds current documents or eligible memories without required retrieval state
- **THEN** the read switch remains disabled and the deployment reports the missing counts

#### Scenario: Migration is interrupted
- **WHEN** a backfill or cleanup worker stops during a bounded batch
- **THEN** it resumes from persisted progress without duplicating active records or modifying unrelated scopes

### Requirement: Conversation-memory cleanup is recoverable
Historical cleanup SHALL provide dry-run counts, explicit target scope, protected document sources, export or rollback evidence, and post-run verification. It SHALL retire duplicate, superseded, expired, or disallowed assistant-derived memories from ordinary recall without deleting visible transcripts or uploaded documents.

#### Scenario: Dry-run is reviewed
- **WHEN** an operator runs cleanup without execution authorization
- **THEN** the report lists targeted and protected counts without changing active data

#### Scenario: Cleanup completes
- **WHEN** an authorized cleanup batch completes
- **THEN** post-run checks show targeted memories absent from ordinary recall and protected documents, transcripts, and unrelated memories unchanged

### Requirement: Retrieval deadline hierarchy includes automatic context work
The deployment SHALL validate that automatic routing and conversation-memory recall, deep-tool execution, optional fallback, and final-answer reserve fit below the agent turn deadline. Each independently configurable timeout MUST be positive and the composed worst-case path MUST leave the configured answer reserve.

#### Scenario: Invalid composed deadlines
- **WHEN** configured routing, memory recall, tool, fallback, and reserve budgets cannot fit within the turn deadline
- **THEN** the sidecar fails startup with a diagnostic identifying the invalid hierarchy
