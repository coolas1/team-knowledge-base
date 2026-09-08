# Edit runbook — Agent A (Stage 1: EDIT)

> **Argument.** `<knowledge-base>` is the system under test. **Default `gbrain`.**
> Replace `<knowledge-base>` (and `<knowledge-base>-files`) everywhere below with
> the app you are evaluating. Future apps reuse this runbook unchanged.

## Your job

You are **Agent A**. Your only job is to edit the corpus per the update
prompts. You do not answer retrieval questions, and you do not score anything.

This benchmark tests the **knowledge base's ability to support edits** — how
well it handles file modification, propagates changes into the knowledge
index, and serves the updated information to a fresh retrieval agent. The
corpus is a copy of the "Wren Adachi" personal archive (Markdown, PDF, Excel,
CSV, PNG, JPG). Every edit is grounded in that content.

After you finish, the operator will run Stage 2 (Agent B verification) and
Stage 3 (scoring) separately.

## What you do

### Per-entry loop

For each entry `NN` in `eval/file-update/entries/` (01–20):

1. **Create a temp workspace.** Copy the full `raw/` corpus to a temporary
   location (e.g. `/tmp/file-update-NN/`). The copy must preserve the exact
   directory structure of `raw/` (`research/`, `music/`, `fermentation/`,
   `journal/`, `notes/`, `travel/`).

2. **Read the update prompt.** Open `eval/file-update/entries/NN/prompt.md`.
   It contains a natural-language user request to change something in the
   corpus.

3. **Find the correct file(s).** The prompt may name specific files, or it may
   be vague (e.g., "update this everywhere"). Search the temp copy to locate
   all files that need editing. Read the files to understand the current
   content before editing.

4. **Apply the edit.** Modify the file(s) in the temp copy. Be precise:
   change only what the prompt asks, not adjacent content.

5. **Write back.** Save the edited file(s) to the temp copy.

6. **Re-ingest (KB-aware apps only).** If `<knowledge-base>` requires ingested
   content to be re-indexed after file changes (e.g. gbrain, WeKnora), run the
   app's re-ingest command on the changed file(s). For file-based PKMs that
   read directly from disk, this step is unnecessary.

7. **Log your trail.** Write a concise log to
   `<knowledge-base>-files/file-update/edit-trails/NN-log.md`:

   ```markdown
   ---
   id: NN
   agent: A
   entry: eval/file-update/entries/NN-short-descriptor
   corpus_copy: /tmp/file-update-NN
   knowledge_base: <knowledge-base>
   ---

   # Edit trail: FU-NN

   ## Files read
   - path/to/file1 (full file)
   - path/to/file2 (lines N–M)

   ## Edits applied
   - path/to/file1: line N — changed "old" to "new"
   - path/to/file2: lines N–M — updated dates per prompt

   ## Files edited
   - path/to/file1
   - path/to/file2

   ## Re-ingest
   - Called `<ingest-command>` on path/to/file1, path/to/file2
   - (or) Not needed (file-based PKM)
   - (or) Re-ingest failed: <reason>

   ## Notes
   <Any edge cases, partial edits, inability to edit certain formats.>
   ```

8. **Save edited files for scoring.** Copy the edited files to
   `<knowledge-base>-files/file-update/edit-outputs/NN/` (preserving the
   relative path under the temp copy). This lets the scoring agent compare
   your output against the golden reference without accessing the temp copy.

### What you MAY do

- **Read** `eval/file-update/entries/NN/prompt.md` (all 20). This is your
  only allowed input file.
- **Read and write** any file in the temp copy (`/tmp/file-update-NN/`).
- **Use `<knowledge-base>` tools** to search, query, or re-ingest content.
  Use whatever tools the registered KB exposes.
- **Use file-system tools** to read, write, and copy files in the temp copy.
- **Iterate.** Some entries require multiple files to be updated — read each
  file, apply its change, and confirm the edit.

### What you MUST NOT do

To keep the score a true measure of the KB — not of your access to the key —
you are forbidden from reading any source of ground truth:

- ❌ `raw/` — the original source corpus. Work ONLY in the temp copy.
- ❌ `eval/file-update/entries/NN/verify-q/` — the verification questions.
- ❌ `eval/file-update/entries/NN/verify-a/` — the answer keys.
- ❌ `eval/file-update/entries/NN/golden/` — the expected edit output.
- ❌ `eval/file-update/entries/NN/rubric.md` — scoring metadata.
- ❌ `eval/file-update/README.md` — contains the entry index and grading rules
  (spoils inconsistencies).
- ❌ `eval/STORY.md`, `eval/STORY_zh.md`, `eval/STORY_ja.md`,
  `scripts/generation/scaffold-personal.py`, and any other corpus metadata or
  generator file.
- ❌ Any non-`<knowledge-base>` tool that reads files outside the temp copy
  or the prompt file.

### Edge cases

- **Binary files (PDF, XLSX, PNG).** You may not be able to edit these
  formats directly. If you cannot edit a binary, state that clearly in your
  log. Honest admission is better than a fabricated edit. If a markdown mirror
  exists, edit that instead and note the limitation.
- **XLSX files.** These can be edited programmatically (e.g., Python +
  openpyxl) if such tools are available. If not, note the inability and edit
  any related markdown mirrors.
- **Creating new files.** Some entries ask you to create a new file (e.g., a
  summary document). Create it in the appropriate directory under the temp
  copy. If the KB requires ingestion of new files, do that too.
- **Partial edits.** If a prompt requires editing 3 files and you can only
  find 2, edit the 2 you found and note the gap. The scoring handles partial
  edits naturally.

## When you're done

All 20 `NN-log.md` files exist under
`<knowledge-base>-files/file-update/edit-trails/`, and all 20
`edit-outputs/NN/` directories contain the edited files. Hand off to the
operator for Stage 2 (Agent B verification) and Stage 3 (scoring).
