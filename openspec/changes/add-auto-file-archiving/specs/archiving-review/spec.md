## Purpose

Defines the human-facing archiving surface: review and deferred files, editable
policy, Sortio-style legacy migration, operation history, undo, and directory
tree browsing.

## ADDED Requirements

### Requirement: The SPA exposes an archiving page
The SPA SHALL include an archiving page reachable from the main navigation,
showing review, unarchived files, legacy migration, editable policy, operation
history and the archive tree. Review-all mode SHALL remain visible and
switchable.

#### Scenario: Navigation entry exists
- **WHEN** a user opens the SPA
- **THEN** the main navigation contains an archiving entry leading to the
  archiving page

#### Scenario: Mode is visible and switchable
- **WHEN** the archiving page is open
- **THEN** the current mode is displayed and the user can switch between auto
  and review-all, taking effect for subsequent classifications

### Requirement: The review queue lists awaiting decisions with their evidence
For each file awaiting review, the page SHALL show the file name, extracted
summary, proposed existing or new directory, confidence, routing reason,
policy version, and retrieval evidence. The user SHALL be able to approve,
defer, or reassign the plan.

#### Scenario: Approve executes the plan
- **WHEN** a user approves a queued plan
- **THEN** the plan runs through the same validation and execution path as an
  auto-approved one, and the entry leaves the queue

#### Scenario: Defer leaves an unmoved file in inbox
- **WHEN** a user chooses "暂不归档"
- **THEN** the file remains where it always was in inbox, becomes
  `unarchived`, and can be replanned later

#### Scenario: Reassign overrides the AI decision
- **WHEN** a user reassigns a queued plan to a different directory
- **THEN** the plan executes against the user-chosen directory with the
  decision source recorded as human

### Requirement: History shows executed operations with undo
The history view SHALL list executed archive operations with timestamp, file
name, source and destination, confidence, decision source (auto, review, or
manual), and linked knowledge-base document, and SHALL offer undo per
operation subject to the journal's conflict rules.

#### Scenario: History row reflects the operation
- **WHEN** an archive operation executes (auto or approved)
- **THEN** a history row appears with its decision metadata

#### Scenario: Undo from history
- **WHEN** a user triggers undo on a history row whose destination file is
  unmodified
- **THEN** the move is reversed, the linked document path is updated to the
  source path, its knowledge index is retained, and history is updated

### Requirement: Deferred files are visible and replannable
Deferred files SHALL be listed with their prior proposal and policy version.
The user SHALL be able to replan or manually assign them through the same
execution path.

#### Scenario: Manual archive of a deferred file
- **WHEN** a user picks a directory for an unarchived file
- **THEN** the file is moved, journaled, and its existing Document path is
  updated, with decision source recorded as manual

### Requirement: Legacy archiving requires preview and selection
The SPA SHALL expose legacy scan results, policy-based dry-run plans, and
all/partial selection. No existing file SHALL move merely because legacy
archiving was enabled or the page was opened.

#### Scenario: User confirms a subset
- **WHEN** the user selects only some dry-run rows and confirms
- **THEN** only those files move and each result remains individually undoable

### Requirement: Users can edit the active archive policy
The SPA SHALL expose the project-first default policy and allow a new version
to be saved, including whether high-confidence new directories are allowed.

#### Scenario: Saving policy affects future planning
- **WHEN** a user saves policy changes
- **THEN** new jobs use the new version while completed and pending plans remain
  unchanged until explicitly replanned

### Requirement: The BFF exposes an archiving API surface
The BFF SHALL expose REST endpoints under `/api/archive` covering: review
approve/defer/reassign, unarchived listing/replan, policy get/update, legacy
scan/plan/execute, history/undo/mode, and directory tree listing.
All mutating endpoints SHALL validate their inputs against the same safety
rules as the pipeline.

#### Scenario: API and pipeline share execution semantics
- **WHEN** an approve or manual-assignment request arrives over the API
- **THEN** it executes through the same validation and journaling as automatic
  archiving

#### Scenario: Tree listing reflects the workspace
- **WHEN** the directory tree endpoint is called
- **THEN** it returns the current archive directory structure with per-folder
  profile information (indexed document counts and descriptions)

#### Scenario: Nested directories can be explored
- **WHEN** the archive tree contains nested paths
- **THEN** the SPA groups them into parent and child folders and lets the user
  expand or collapse every folder that has children

#### Scenario: Archived files are browsable from their directory
- **WHEN** a user expands a directory containing indexed documents
- **THEN** the SPA lists each document with its title, type and status, and
  selecting a document opens the existing knowledge-base document detail page
