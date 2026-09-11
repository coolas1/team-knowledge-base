## Purpose

Define recoverable removal and reconstruction of legacy file-derived observations from summary facts, preserving original documents and unrelated conversational evidence.

## ADDED Requirements

### Requirement: Migration selection is explicit and previewable
The system SHALL offer a read-only migration preview with target bank, document identities, expected revisions, old facts, affected observation evidence closure and estimated workload. Preview SHALL neither mutate data nor call generation models. Ambiguous provenance SHALL be reported and excluded from automatic destructive processing.

#### Scenario: Mixed corpus preview
- **WHEN** a bank contains legacy files, conversations and already migrated files
- **THEN** preview identifies only legacy file-derived targets and their dependent observations, preserving unrelated and already current content

### Requirement: Legacy file observations are removed and rebuilt
The system SHALL remove affected old observation content from active memory, retrieval, graph projection and cache and rebuild from new summary facts and remaining valid evidence. Old full-text facts SHALL lose active status and SHALL NOT regenerate observations through replay, retry or pending workers. Original file text, raw vectors and unrelated conversation facts SHALL be preserved. Audit-only history SHALL NOT be used as current evidence.

#### Scenario: File-only observation
- **WHEN** an observation depends only on selected legacy file facts
- **THEN** its old active content is removed and any replacement is traceable to the new summary revision

#### Scenario: Mixed conversation and file evidence
- **WHEN** an observation contains both targeted file facts and conversation facts
- **THEN** the old observation content is removed and recomputed while the conversation facts remain intact

#### Scenario: Derived mental model
- **WHEN** a mental model depends on retired facts or observations
- **THEN** it stops being valid evidence until rebuilt from current sources

### Requirement: Rebuild is resumable and fenced
The system SHALL persist migration progress, enforce document revision checks and prevent old workers from committing superseded results. It SHALL support bounded batches, budget-based pause and idempotent resume, reusing completed summaries. A changed document SHALL require replanning rather than overwriting its new content.

#### Scenario: Crash after retirement
- **WHEN** execution stops after retiring old evidence but before new consolidation finishes
- **THEN** resume completes remaining stages without duplicate active facts or repeated successful summary generation

#### Scenario: Concurrent old worker
- **WHEN** a worker attempts to commit based on retired evidence
- **THEN** its write is rejected and retired observation content remains unavailable

### Requirement: Migration completion is verifiable and recoverable
Migration SHALL export recoverable scoped state before destructive processing and report per-document outcomes, token usage, failures and remaining work. Success SHALL require zero active references to targeted old facts, completed projection cleanup, current summary provenance and unchanged non-target content. Empty extraction SHALL have an explicit valid terminal outcome. Recovery SHALL NOT overwrite intervening writes.

#### Scenario: Verification detects residual evidence
- **WHEN** an old active edge or projection remains after processing
- **THEN** the job is not reported complete and exposes the remaining cleanup work

#### Scenario: Budget reached
- **WHEN** the configured run token or cost budget is exhausted
- **THEN** new model calls stop and the persisted run can resume from its recorded progress
