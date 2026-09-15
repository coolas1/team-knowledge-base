## ADDED Requirements

### Requirement: Vector indexed files use versioned summary memory
The system SHALL default to preserving and vector-indexing complete file text while deriving file memory facts and observations only from a bounded summary. It SHALL persist the source hash, summary policy version and coverage alongside the summary. File chunk entity extraction SHALL be disabled in this mode; conversation retention SHALL retain its existing behavior.

#### Scenario: File with details outside summary
- **WHEN** a file is indexed under the summary policy
- **THEN** its complete text remains vector-searchable, its memory input is the summary, and omitted details are not invented as facts

#### Scenario: Long document sampling
- **WHEN** the source exceeds the configured summary input budget
- **THEN** the summary reports partial coverage, includes sampling from the document tail, and summary input/output remain bounded

### Requirement: File entrypoints share summary identity
Upload, edited reindex, historical backfill, retry and reprocessing SHALL apply the same file-memory policy. A successful summary SHALL be reused for matching source hash and policy identity. Failed summaries SHALL NOT become factual memory or trigger fallback to full-text consolidation.

#### Scenario: Retry unchanged file
- **WHEN** retention retries after a summary was successfully persisted
- **THEN** the summary is reused without another summary-model call and old full-text snapshots cannot reintroduce full-text facts

#### Scenario: Source changed
- **WHEN** content hash or summary policy changes
- **THEN** a new summary revision is generated and superseded memory inputs stop being active evidence
