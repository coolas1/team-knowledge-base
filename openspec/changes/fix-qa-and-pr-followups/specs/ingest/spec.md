## ADDED Requirements

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
