# ingest Specification

## Purpose

Defines document ingestion behavior: bounded parallel processing that produces deterministic results, batched knowledge-graph writes that preserve single-write semantics, and batch ingest operations across the engine contract, CLI, and BFF surfaces with per-file failure isolation.

## Requirements

### Requirement: Ingest parallelism is bounded by two independent knobs
The system SHALL process ingestion work concurrently, bounded by two independently configurable limits: chunk-level concurrency (per-document chunk analysis and embedding) and document-level concurrency (documents in the extraction/analysis stage). Both SHALL be settable via the app configuration (`engine.ingest.chunk_concurrency`, `engine.ingest.doc_concurrency`), SHALL have defined defaults (4 and 2), and SHALL accept 1 to force serial processing of that stage. Values below 1 SHALL be treated as 1.

#### Scenario: Chunk stage bounded
- **WHEN** a document with many chunks is ingested
- **THEN** chunk analysis and embedding tasks run concurrently, with at most `chunk_concurrency` in flight per document

#### Scenario: Document stage bounded
- **WHEN** several documents are being ingested at the same time
- **THEN** at most `doc_concurrency` documents occupy the extraction/analysis stage simultaneously

#### Scenario: Serial mode
- **WHEN** both knobs are set to 1
- **THEN** ingestion processes one chunk and one document at a time, matching the pre-parallel behavior

### Requirement: Parallel processing preserves deterministic results
Concurrent chunk analysis and embedding SHALL produce the same persisted state as serial processing: chunk analyses stored in original chunk order, embeddings aligned to their chunks by position, and progress observable as a completed/total count.

#### Scenario: Order is preserved under parallelism
- **WHEN** a document is ingested with chunk concurrency greater than 1
- **THEN** chunks, their analyses, and their embeddings are persisted in the original chunk order

#### Scenario: Failure cancels the document, not the corpus
- **WHEN** one chunk's analysis raises during parallel processing
- **THEN** the document is marked failed with the underlying error, no partial chunks are persisted for it, and other documents continue

### Requirement: Reindexing uses the same parallel path
Re-ingesting an edited document SHALL run through the same bounded-parallel analysis as initial ingestion, skipping only text extraction.

#### Scenario: Document edit triggers reindex
- **WHEN** an indexed document's content is edited and reindexed
- **THEN** its analysis runs under the same concurrency bounds and produces the same result shape as the initial ingest

### Requirement: Knowledge-graph writes are batched
During ingestion, entity and relation writes SHALL be batched: items grouped by type, one set-based write per group, plus at most one source-annotation write-back per batch. Batched writes SHALL produce a graph identical to writing the same items one at a time.

#### Scenario: Batched and per-item writes are equivalent
- **WHEN** the same chunk analyses are written through the batched path and through the per-item path
- **THEN** the resulting entities, relations, and source annotations are identical

#### Scenario: Round trips scale with types, not items
- **WHEN** a document's analysis yields many entities spanning a few types
- **THEN** the graph write issues one set-based operation per type rather than one operation per entity

### Requirement: Batch ingest with per-file failure isolation
The engine contract SHALL provide a batch ingest operation that accepts a list of sources and returns one result per source, in order. Each source SHALL be processed independently: a failure on one source SHALL be captured in that source's result and SHALL NOT prevent the remaining sources from being processed.

#### Scenario: One bad file among many
- **WHEN** batch ingest receives three files and the second cannot be processed
- **THEN** files one and three ingest normally and file two's result reports failure with its error

#### Scenario: All files valid
- **WHEN** batch ingest receives only valid files
- **THEN** each file returns its own document reference with processing status

### Requirement: BFF batch upload endpoint
The BFF SHALL expose a batch upload endpoint accepting multiple files in one request. Each file SHALL be validated and ingested independently; the response SHALL carry one entry per file reporting either success (with the document reference) or failure (with the reason). An empty file list SHALL be rejected as a request-validation error.

#### Scenario: Mixed batch
- **WHEN** five files are uploaded and one has an unsupported type
- **THEN** four entries report success with document references and one reports the rejection reason for that file

#### Scenario: Empty batch
- **WHEN** the request contains no files
- **THEN** the endpoint rejects it with a validation error (422)

### Requirement: CLI batch ingest with terminal-status polling
The CLI SHALL provide a batch ingest command that takes a list of files, enqueues them all, and then polls each document until it reaches a terminal status (`indexed` or `failed`) or a configurable timeout expires, printing the final per-file status.

#### Scenario: All files complete within timeout
- **WHEN** all enqueued files reach a terminal status before the timeout
- **THEN** the command prints each file's terminal status

#### Scenario: Timeout expires
- **WHEN** a file has not reached a terminal status when the timeout expires
- **THEN** the command reports that file's last observed status

#### Scenario: Missing file
- **WHEN** a given path does not exist
- **THEN** the command fails fast with an error naming the path, before ingesting anything

### Requirement: Multi-file upload from the web client
The web client SHALL upload a multi-file selection as one batch request through the batch endpoint and surface per-file outcomes.

#### Scenario: User selects several files
- **WHEN** a user selects multiple files in the upload control and confirms
- **THEN** the client sends a single batch request and reports each file's success or failure individually

### Requirement: Graph write completes and records version state
Under the deployed graph-projection configuration, a full ingest SHALL complete the knowledge-graph write stage — a document whose text extracts and analyzes successfully SHALL reach `indexed` status, not fail after Postgres persistence — and each version's Document node in the graph SHALL record the version number and whether it is the current version.

#### Scenario: Fresh upload indexes under the deployed graph projection
- **WHEN** a supported file is uploaded through the public API on the deployed configuration
- **THEN** the document transitions to `indexed` status with its graph nodes written, rather than failing at the graph-write stage

#### Scenario: Document node carries version properties
- **WHEN** a document version is ingested or superseded
- **THEN** that version's Document node in the graph records its `version_number` and `is_current` values, so a superseded version is marked not current

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

### Requirement: Transient analysis failures are retried with backoff
During extraction, chunk analysis, embedding, and reindexing, transient failures of external model endpoints (timeouts, connection errors, rate limiting) SHALL be retried with bounded exponential backoff before the document is marked failed. The retry count and backoff parameters SHALL be configurable in the app configuration.

#### Scenario: Saturated model endpoint self-heals
- **WHEN** bulk ingest saturates the model endpoint and per-call requests time out transiently
- **THEN** affected calls are retried with backoff and the documents reach indexed status without operator intervention

#### Scenario: Failure persists past the retry budget
- **WHEN** an analysis or embedding call keeps failing until the retry budget is exhausted
- **THEN** the document is marked failed with the underlying error

### Requirement: Failed documents carry a non-empty error
A document marked failed SHALL carry a non-empty error message. When the underlying exception has an empty string form (for example a cancellation), the recorded error SHALL at minimum identify the failure by exception type or stage.

#### Scenario: Message-less exception
- **WHEN** the pipeline fails with an exception whose string form is empty
- **THEN** the document's error message still identifies what failed

### Requirement: Background ingest tasks cannot die silently
Background ingest and reindex work — including the reindex of a new version produced by a versioned edit — SHALL keep a strong reference to the task for its lifetime, and a task that fails outside the pipeline's own terminal-status handling SHALL cause the affected document row (the new version row, for a versioned edit) to be marked failed with a non-empty error and the failure to be logged.

#### Scenario: Background task fails early
- **WHEN** a scheduled ingest or reindex task raises before the pipeline records a terminal status
- **THEN** the document ends in failed status with a non-empty error and the failure is logged

#### Scenario: Task outlives the request
- **WHEN** an upload request has returned and processing continues in the background
- **THEN** the background task is not garbage-collected mid-flight and runs to a terminal status

#### Scenario: Failed versioned edit is recoverable
- **WHEN** a versioned edit's background reindex fails or dies after the previous version was demoted
- **THEN** the new version row is marked failed with a non-empty error and re-triggering reprocessing reindexes it

### Requirement: Upload size is capped at the BFF
The BFF upload endpoints (single and batch) SHALL reject uploads larger than a configurable maximum (default 100 MiB) with 413 and a reason, without buffering content beyond the cap.

#### Scenario: Oversized upload rejected
- **WHEN** a client uploads a file larger than the configured maximum
- **THEN** the endpoint responds 413 with a reason and no document is created

#### Scenario: Batch isolates the oversized file
- **WHEN** a batch upload contains one file over the cap alongside normal files
- **THEN** the oversized file's entry reports the 413 rejection and the other files ingest normally

### Requirement: Image documents pass an OCR quality gate
An image document whose OCR extraction yields no usable text SHALL be marked failed with an actionable message explaining that no usable text was extracted, instead of being indexed with garbage text.

#### Scenario: Textless screenshot
- **WHEN** an image with no meaningful text content is uploaded
- **THEN** the document is marked failed with the OCR quality message

#### Scenario: Adequate OCR text
- **WHEN** an image with sufficient readable text is uploaded
- **THEN** the document indexes normally

### Requirement: Extraction dependency errors are platform-appropriate
Error messages that suggest installing missing extraction tooling SHALL name an install method appropriate to the running platform.

#### Scenario: OCR missing on a Linux deployment
- **WHEN** OCR is unavailable while ingesting an image on Linux
- **THEN** the error suggests a Linux-appropriate install command, not a macOS package manager
