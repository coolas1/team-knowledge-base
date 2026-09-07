## Purpose

Defines document ingestion behavior: bounded parallel processing that produces deterministic results, batched knowledge-graph writes that preserve single-write semantics, and batch ingest operations across the engine contract, CLI, and BFF surfaces with per-file failure isolation.

## ADDED Requirements

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
