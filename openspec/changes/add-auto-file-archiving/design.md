# Design: add-auto-file-archiving V2

## Context

The existing system already extracts files, creates a document overview, analyzes
each chunk for entities and relations, embeds chunks, and writes Postgres and
Neo4j. Archiving is a separate concern: preserving and organizing the original
file. V1 serialized these concerns and re-extracted the file after moving it.
V2 shares one immutable FileContext and runs archive classification concurrently
with knowledge analysis.

## Goals

- Extract a stable inbox file once and safely parallelize archive classification
  with the existing GraphRAG analysis.
- Use only two confidence outcomes: high-confidence automatic execution and
  low-confidence user confirmation.
- Permit policy-controlled automatic creation of new directories.
- Keep deferred files searchable while they remain in inbox.
- Provide an editable, versioned project-first archive policy.
- Provide Sortio-style scan, dry-run, selection, and batch archiving for existing
  unarchived documents without rebuilding their knowledge indexes.
- Journal and reverse every physical move.

## Non-Goals

- Editing the contents of PDF/DOCX/PPTX originals.
- Silently reconstructing missing binary originals from extracted text.
- Automatically reorganizing completed operations when a policy changes.
- Letting an LLM directly execute filesystem operations.

## Decisions

### D1: One extraction, two concurrent branches

After scanner stability and hash checks, the coordinator creates:

```text
FileContext(raw_text, content_hash, source_path, file metadata)
        |                                  |
        v                                  v
archive classification                 knowledge analysis
folder retrieval + LLM                 overview + chunk LLM + embeddings
plan + validate                        Postgres + Neo4j
```

`IngestSource` (or an equivalent `PreparedIngest`) carries trusted extracted
text. The GraphRAG pipeline must not re-read a path that the archive branch may
move. Branch failures are independent and recorded separately.

Knowledge analysis creates one Document linked to the inbox source. An archive
move updates that Document's `file_path`; it never starts a second ingest.

### D2: Binary confidence routing

```text
confidence >= threshold -> auto
confidence <  threshold -> awaiting_review
review_all               -> awaiting_review
```

Both branches must produce a complete plan selecting an existing directory or
proposing a new one. A high-confidence new directory may auto-execute when the
active policy allows it and Validator passes.

Review actions are approve, reassign, and defer. Defer means `unarchived`: the
file never left inbox and remains linked to its searchable Document. It may be
replanned later. There is no `skipped` confidence state and no "return to inbox"
transition.

### D3: Existing-folder reuse and new-folder creation are symmetric choices

Directory retrieval narrows the existing side of the decision to Top-K; it does
not decide that an existing directory must be used. Every classification prompt
offers two mutually exclusive outcomes:

```text
reuse_existing(candidate_id)  OR  create_new(new_subdirectory)
```

The LLM compares both outcomes on every file, regardless of whether the archive
tree is empty. If a retrieved folder is semantically appropriate it should be
reused. If the file represents a distinct project or stable topic, a new semantic
directory should be proposed even when existing candidates are present. With an
empty archive tree, the first files bootstrap meaningful project/topic folders;
they are never silently assigned to a catch-all folder.

Catch-all operational folders such as `待整理`, `待确认`, and `未分类` remain
visible in the tree and history but are excluded from semantic retrieval, so they
cannot become attractors for later classifications. If the LLM is unavailable,
the system returns a visible, retryable, non-executable classification failure;
it does not manufacture a `待整理` plan.

For a multi-file cold start, each file receives both choices independently. The
preview may therefore propose several new semantic directories, while files that
belong to the same clearly identified project should converge on the same
normalized directory name. The `allow_new_directory` policy controls whether a
high-confidence new directory may execute automatically; it never removes the
new-directory option from classification or review.

### D4: Policy remains data, not hard-coded prompt text

`archive_policies` stores versioned instructions, priority, optional deterministic
match/template data, `allow_new_directory`, and fallback behavior. The default
policy is project-first: confidently identified projects prefer
`项目/<项目名>`; otherwise semantic folder-profile retrieval is used.

Every plan and operation records policy id/version. Saving a policy affects new
jobs only. Pending or deferred jobs change only through explicit replan.

### D5: Planner and Validator retain filesystem authority

The LLM returns structured candidate/new-subdirectory/name/confidence/rationale
data. Planner resolves it. Validator enforces archive-root containment, traversal
protection, safe names, collision policy, source hash, permissions, and allowed
directory depth. Only Executor may mkdir or move.

### D6: Undo reverses organization, not knowledge

Undo verifies destination hash, moves the file back, and updates the existing
Document path. The knowledge index is retained because it was created in the
parallel knowledge branch, not by the archive operation. "Undo and remove from
knowledge base" is an explicit separate action.

### D7: Existing-document migration is preview-first

Eligible sources are current public Documents outside archive, not already
managed by an operation, whose physical source exists. Scan reports eligible,
already archived, missing-source, and conflict groups. Planning is a dry-run.
Users select all or individual rows and may reassign before confirmation.

Execution is per item: move, journal, update the existing Document path. It
preserves document id, chunks, embeddings, versions, and Neo4j nodes. Batch rows
record independent success/failure and support retry and per-operation undo.

### D8: UI and API

ArchivePage exposes: pending confirmation, unarchived, legacy migration, policy,
history, and directory tree. Required APIs include:

- reviews: list/approve/defer/reassign;
- unarchived: list/replan/manual assignment;
- policies: get/update;
- legacy: scan/plan/execute and batch status;
- operations: list/undo;
- mode and tree.

## Migration

1. Add nullable job/operation policy and kb_doc fields plus policy/migration
   tables using init_db-compatible schema creation.
2. Existing `skipped` jobs map to `unarchived`; existing awaiting-review jobs keep
   their stored plan until explicitly replanned.
3. Remove delta from runtime behavior while accepting the old config key for one
   compatibility release with a deprecation warning.
4. Existing completed operations keep V1 undo semantics as recorded; new V2
   operations use path-relink undo semantics.

## Risks

- Moving while analysis reads the file: eliminated by immutable FileContext.
- Knowledge succeeds while classification fails: Document remains searchable in
  inbox and the archive error is visible/retryable.
- Batch migration moves the wrong files: preview, explicit selection, per-item
  validation, journal, and undo.
- New-directory proliferation: policy switch, confidence threshold, depth/name
  constraints, history, and undo.
- Missing legacy originals: report only; never fabricate binary originals.
