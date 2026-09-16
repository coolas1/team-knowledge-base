## Purpose

Defines automatic file archiving: files arriving in a watched inbox are classified
against an archive directory tree by retrieval plus an LLM structured decision,
then either auto-executed or queued for user confirmation. Extraction happens
once and archive classification runs concurrently with knowledge analysis.
Every move is journaled and reversible.

## ADDED Requirements

### Requirement: Files enter the archive workspace through two entry points
The system SHALL accept files into an inbox directory inside a persistent
workspace via (a) a web upload that routes to the inbox and (b) files placed
directly on the host into the mounted inbox directory. The existing direct
document-ingest upload API SHALL remain available and unchanged.

#### Scenario: Web upload goes to the inbox
- **WHEN** a user uploads a file through the SPA upload control
- **THEN** the file is stored in the inbox and processed by the archiving
  pipeline rather than being ingested directly

#### Scenario: Host-placed file is discovered
- **WHEN** a file is placed into the mounted inbox directory from the host
- **THEN** the archiving pipeline discovers and processes it without any user
  action in the web UI

#### Scenario: Direct ingest API still works
- **WHEN** a client posts a file to the direct document upload endpoint
- **THEN** the file is ingested into the knowledge base exactly as before,
  bypassing the archiving pipeline

### Requirement: A file is processed only after it is stable and unseen
Before any classification, the system SHALL wait until an inbox file is stable
(size and modification time unchanged across consecutive checks), SHALL ignore
temporary/hidden file patterns (e.g. `.part`, `.crdownload`), and SHALL skip
files whose content hash has already been processed.

#### Scenario: Still-writing file waits
- **WHEN** a file appears in the inbox but its size keeps changing across
  stability checks
- **THEN** the file is not enqueued for classification until it passes
  consecutive stability checks or a maximum wait is reached

#### Scenario: Temporary files are ignored
- **WHEN** a partially-downloaded file with a temporary suffix appears in the
  inbox
- **THEN** it is never enqueued

#### Scenario: Duplicate content is skipped
- **WHEN** a file whose content hash was already processed appears again in the
  inbox (under any name)
- **THEN** no new archive job is created for it

### Requirement: Archive work runs on a persistent, retrying job queue
Discovered files SHALL be recorded as jobs in a persistent queue with status,
attempt count, and retry with backoff; a crashed or restarted process SHALL
resume queued work without losing jobs, and jobs that repeatedly fail SHALL
reach a terminal failed state visible to operators.

#### Scenario: Restart preserves pending work
- **WHEN** the backend restarts while archive jobs are queued or in progress
- **THEN** those jobs are resumed or reclaimed after restart

#### Scenario: Repeated failure terminates the job
- **WHEN** a job fails more than the configured maximum attempts
- **THEN** the job stops being retried and its failure is recorded and visible

### Requirement: Classification uses candidate retrieval plus a structured LLM decision
For each stable file, the system SHALL build a file context (extracted text and
metadata using the same extraction layer as document ingest), retrieve the
top-K candidate archive directories by semantic similarity between the file
context and directory profiles, and ask the LLM to compare two symmetric
outcomes: reuse one offered existing directory or create a new semantic
subdirectory. Both outcomes SHALL be considered regardless of whether existing
directories are available. The LLM SHALL NOT produce raw destination paths; its
output SHALL be a structured, mutually exclusive decision referencing either a
candidate identifier or a new subdirectory under an allowed root, plus a
confidence value and rationale. Operational catch-all directories SHALL NOT be
used as semantic retrieval candidates.

#### Scenario: Decision references a candidate
- **WHEN** the LLM responds to a classification request
- **THEN** the parsed decision references one of the offered candidate
  identifiers (or a permitted new subdirectory), and the backend resolves it to
  a real destination path

#### Scenario: Existing matching directory is reused
- **WHEN** a retrieved directory is semantically appropriate for the file
- **THEN** the decision may reference that existing candidate rather than
  creating a duplicate directory

#### Scenario: Existing directories do not fit
- **WHEN** existing candidates are present but the file belongs to a distinct
  project or stable topic
- **THEN** the decision may propose a new semantic subdirectory instead of
  forcing the file into an existing candidate

#### Scenario: Empty archive tree bootstraps semantic directories
- **WHEN** no semantic directory candidates exist
- **THEN** the decision proposes a meaningful project or topic directory and
  does not default to `待整理`, `待确认`, or `未分类`

#### Scenario: Multiple files cold-start independently
- **WHEN** multiple files are classified while the semantic directory tree is
  empty
- **THEN** every file independently compares reuse and create outcomes, the
  preview may contain multiple new semantic directories, and files are not all
  assigned to one catch-all directory

#### Scenario: Catch-all directory is not a semantic candidate
- **WHEN** an operational catch-all directory exists in the archive tree
- **THEN** it remains visible for operations and history but is excluded from
  Top-K semantic candidates for subsequent classifications

#### Scenario: LLM unavailable degrades gracefully
- **WHEN** no LLM is configured or the call fails persistently
- **THEN** files are not silently auto-archived or assigned to a catch-all;
  they remain in the inbox with a visible, retryable, non-executable failure

#### Scenario: Malformed LLM output never executes
- **WHEN** the LLM output cannot be parsed into a valid structured decision
- **THEN** no archive action is executed for that file and the job fails
  visibly with the parse error

### Requirement: Confidence dispatches files to auto or user confirmation
The system SHALL use one configurable threshold. Confidence at or above the
threshold SHALL auto-execute; confidence below it SHALL enter the review queue
with a complete recommendation. A recommendation MAY select an existing folder
or propose a new folder. A review-all test mode SHALL route every file to review.

#### Scenario: High confidence auto-archives
- **WHEN** a file's classification confidence is at or above `threshold`
  and auto mode is on
- **THEN** the archive plan is validated and executed without human interaction

#### Scenario: Low confidence awaits confirmation
- **WHEN** a file's confidence is below `threshold`
- **THEN** the file stays in inbox and the review queue shows the proposed
  existing or new directory until a user approves, reassigns, or defers it

#### Scenario: High-confidence new directory auto-executes
- **WHEN** a high-confidence decision proposes a policy-permitted new directory
- **THEN** the Validator checks and creates it before moving the file without
  forcing review merely because the directory is new

#### Scenario: Policy disallows automatic new-directory creation
- **WHEN** a decision proposes a new directory but the active policy disallows
  automatic creation
- **THEN** the proposal remains available for user confirmation instead of
  being removed from classification or forced into an existing directory

#### Scenario: Review-all mode intercepts everything
- **WHEN** review-all mode is enabled
- **THEN** every classified file enters the review queue, including
  high-confidence ones

### Requirement: Plans are validated before any file moves
Before execution, every archive plan SHALL pass safety validation: destination
resolved inside the archive workspace boundary, no path traversal, no
destination collision (or a deterministic collision-resolution rule), source
unchanged since classification (content hash match), and required permissions.
A plan that fails validation SHALL NOT execute and SHALL surface the reason.

#### Scenario: Escaped path is rejected
- **WHEN** a plan's resolved destination falls outside the archive workspace
- **THEN** the plan is blocked and the reason recorded

#### Scenario: Source changed mid-flight
- **WHEN** the source file's content hash no longer matches the one classified
- **THEN** the plan is not executed

#### Scenario: Destination collision
- **WHEN** the destination already contains a file with the same name
- **THEN** the plan either resolves the collision deterministically (e.g.
  suffixing) or is blocked, per configuration, never silently overwriting

### Requirement: Executed moves are journaled and reversible
Every executed archive operation SHALL be recorded in an operation journal
with source path, destination path, content hash, decision metadata
(confidence, decision source), and the knowledge-base document identifier
created by the linked ingest. An undo SHALL reverse the move (and remove the
linked knowledge-base document) when the current file at the destination still
matches the journaled hash; if it has been modified since, undo SHALL refuse
and report the conflict.

#### Scenario: Undo reverses an auto-archive
- **WHEN** a user undoes a journaled operation and the destination file is
  unmodified
- **THEN** the file moves back to its original inbox location and the linked
  knowledge-base document is removed

#### Scenario: Undo refuses after external modification
- **WHEN** the archived file was modified after archiving
- **THEN** undo refuses and reports that the file changed since archiving

### Requirement: Archive classification and knowledge analysis run concurrently
After stability checks, the system SHALL extract the file once into an immutable
FileContext. Archive classification and the existing overview/chunk/entity/
relation/embedding analysis SHALL consume that context concurrently. Knowledge
analysis SHALL NOT re-read the source path. The resulting Document SHALL point
to inbox while awaiting review and SHALL be relinked to the archive destination
after a move without duplicate ingest.

#### Scenario: Low-confidence file is searchable before review
- **WHEN** knowledge analysis finishes while archive review is pending
- **THEN** the Document references the inbox file and is searchable

#### Scenario: Approval relinks without re-ingest
- **WHEN** the user approves and the file moves from inbox to archive
- **THEN** the existing Document file_path is updated while its id, chunks and
  graph remain unchanged

### Requirement: Existing unarchived documents support policy-driven migration
The system SHALL scan current public Documents whose source exists outside the
archive tree, classify them with the active versioned policy, and present a dry
run before any batch move. Users SHALL select all or individual plans. Execution
SHALL move each selected original, journal it, and update the existing Document
path without rebuilding its knowledge index.

#### Scenario: Existing documents are previewed safely
- **WHEN** a user enables legacy archiving and requests a scan
- **THEN** the system reports archivable, already archived, missing-source and
  conflicting documents and moves nothing

#### Scenario: Selected existing documents are archived
- **WHEN** the user confirms selected dry-run items
- **THEN** each item is independently moved and journaled and keeps its existing
  document id, chunks and graph

### Requirement: Archive policy is editable and versioned
The system SHALL provide a default project-first policy and allow users to edit
the active policy. Plans and operations SHALL record the policy id and version.
Policy edits SHALL affect new or explicitly replanned jobs only.

#### Scenario: Default project rule
- **WHEN** the classifier confidently identifies a project
- **THEN** it prefers `项目/<项目名>` according to the default policy

#### Scenario: Rule changes do not rewrite history
- **WHEN** a user saves a new policy version
- **THEN** completed operations remain unchanged and pending jobs change only
  after explicit replanning
