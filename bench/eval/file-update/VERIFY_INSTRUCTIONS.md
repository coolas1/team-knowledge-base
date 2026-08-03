# Verify runbook — Agent B (Stage 2: VERIFY)

> **Argument.** `<knowledge-base>` is the system under test. **Default `gbrain`.**
> Replace `<knowledge-base>` (and `<knowledge-base>-files`) everywhere below with
> the app you are evaluating. Future apps reuse this runbook unchanged.

## Your job

You are **Agent B** — a fresh agent instance with **no memory of Stage 1**.
Agent A has already modified the corpus per 20 update prompts. Your job is
to answer pre-written verification questions about the modified corpus,
retrieving answers **through the knowledge base only** — not from any prior
knowledge of what was changed.

This is a test of the **knowledge base's ability to serve updated
information**. If Agent A edited a file and re-ingested it correctly, your
KB queries should return the new values. If the KB still holds old data, your
answers will reflect that — which is useful data for the scorer.

## Core principles (read these first)

1. **Answer only from what the KB retrieves.** Do not fill gaps from your own
   training knowledge, even if a plausible answer is obvious to you. An answer
   the KB cannot justify is a wrong answer here.

2. **You must not know what Agent A changed.** Do not read Agent A's edit
   trails, edit outputs, or any Stage 1 artifacts. If you accidentally see
   one, state what you saw and exit — the run must be redone untainted.

3. **No fabrication.** Never invent a temperature, date, file name, citation,
   or number. If the KB returns nothing usable, say so.

4. **Unknown stays unknown.** A faithful *"I could not find this in the KB"*
   is a **good** outcome; a confident guess is a **failure**. Record what you
   searched and that it came up empty.

5. **Surface contradictions you do find.** If the KB returns conflicting
   values (e.g., an edited file says one thing, an unedited file says
   another), report *all* of them and which source each came from — do not
   silently pick one.

6. **Prior memory of a forbidden source is a stop condition.** If, while
   working on any question, you realize you already know the answer — or any
   ground-truth detail — from having previously seen a forbidden file
   (`eval/file-update/entries/*/verify-a/`, `eval/file-update/entries/*/golden/`,
   `eval/file-update/README.md`, `raw/`, `eval/STORY.md`, or Agent A's output),
   do **not** answer from that memory. Stop, state which file(s) you recall
   and **when/how you learned them**, and exit the verification process
   entirely. Report it to the operator so the run can be redone untainted.

## What you MAY do

- **Read** `eval/file-update/entries/NN/verify-q/NN-vq.md` (all verification
  questions). This is your only allowed input from the benchmark harness.
- **Use `<knowledge-base>` tools** to search, query, read pages, and follow
  links inside the KB. For the default KB these are the `mcp__gbrain__*` tools
  (e.g. `mcp__gbrain__search`, `mcp__gbrain__query`, `mcp__gbrain__recall`,
  `mcp__gbrain__get_page`, `mcp__gbrain__get_backlinks`,
  `mcp__gbrain__get_chunks`). Use whatever read tools the registered KB
  exposes.
- **Use file-system tools** to read files in the modified corpus (the temp
  copy that Agent A prepared), if and only if the KB provides direct file
  access. For KBs that index content into a database, use the KB tools
  instead.
- **Iterate.** Some verification questions require multi-hop retrieval —
  follow the citations the KB returns rather than stopping at the first hit.

## What you MUST NOT do

To keep the score a true measure of the KB — not of your access to the key —
you are forbidden from reading any source of ground truth:

- ❌ `raw/` — the original, unmodified source corpus.
- ❌ `eval/file-update/entries/NN/prompt.md` — the update prompts (reveals
  what Agent A was asked to change).
- ❌ `eval/file-update/entries/NN/verify-a/` — the answer keys.
- ❌ `eval/file-update/entries/NN/golden/` — the expected edit output.
- ❌ `eval/file-update/entries/NN/rubric.md` — scoring metadata.
- ❌ `<knowledge-base>-files/file-update/edit-trails/` — Agent A's edit logs.
- ❌ `<knowledge-base>-files/file-update/edit-outputs/` — Agent A's edited
  files (use the KB, not the raw output).
- ❌ `eval/file-update/README.md` — contains the entry index and grading
  rules (spoils inconsistencies).
- ❌ `eval/file-update/EDIT_INSTRUCTIONS.md` — Agent A's runbook.
- ❌ `eval/STORY.md`, `eval/STORY_zh.md`, `eval/STORY_ja.md`,
  `scripts/generation/scaffold-personal.py`, and any other corpus metadata or
  generator file.
- ❌ Any non-`<knowledge-base>` tool that reads files outside the modified
  corpus and the verification question files.

If a verification question references a path like
`fermentation/recipes.md`, that is a **hint about what to ask the KB for** —
not a file you may open directly (unless the KB provides direct file access
as its retrieval mechanism).

## Per-entry workflow

For each entry `NN` in `eval/file-update/entries/` (01–20):

1. **Read the verification questions.** Open
   `eval/file-update/entries/NN/verify-q/01-vq.md` (and `02-vq.md` if
   present). Each contains a question about the modified corpus.

2. **Query the KB.** Use `<knowledge-base>` tools to retrieve the answer. The
   question may reference specific files or topics — use those as search
   terms. Multi-hop questions may require several tool calls.

3. **Answer precisely.** Write your answer exactly as supported by KB
   retrieval. Be specific: names, models, numbers, dates. If multiple sources
   disagree, list each value and its source.

4. **Write the prediction.** Save to
   `<knowledge-base>-files/file-update/verify-predictions/NN-vp.md`:

   ````markdown
   ---
   id: NN
   agent: B
   entry: eval/file-update/entries/NN-short-descriptor
   knowledge_base: <knowledge-base>
   ---

   # Verification predictions: FU-NN

   ## VQ-NN.01 — <short title>

   ### Answer

   <Your answer, exactly as supported by KB retrieval.>

   ### Retrieval trail

   <The KB tool calls you made and what each returned, in order.>

   ### Confidence / coverage

   <One of: **found** / **partial** / **not found in KB**, plus a one-line reason.>

   ## VQ-NN.02 — <short title>

   ### Answer

   <...>

   ### Retrieval trail

   <...>

   ### Confidence / coverage

   <...>
   ````

## Verification question format

Each `verify-q/NN-vq.md` uses frontmatter:

```markdown
---
id: NN-NN
entry_id: NN
category: TXT | DAT | CFE | CFI | MOD | XLG | SYN | TMP
difficulty: easy | medium | hard
retrieval_type: detail | link | temporal | multi-modal | cross-lingual
---
# VQ-NN.NN — <title>

<question text>
```

The `retrieval_type` field hints at what kind of KB interaction is needed:
- **detail**: single-fact lookup from one file
- **link**: multi-hop traversal across linked files
- **temporal**: date or time-series query
- **multi-modal**: requires engaging a binary (PDF, XLSX, PNG)
- **cross-lingual**: query or source is in JA/ZH

## Tips

- **You don't know what changed.** Approach each question fresh. The answer
  could be the same as the original corpus (regression check), or it could be
  the updated value. Don't assume — retrieve.

- **Multi-modal questions.** The KB's value is parsing binaries (PDF / XLSX /
  PNG). Probe the KB for the binary's content, not just a Markdown mirror.
  Note in your trail whether the answer came from the binary or a mirror.

- **Inconsistencies.** Some entries are designed so edited and unedited files
  disagree. If your KB retrieval surfaces two different values for the same
  fact, that is a feature — report both rather than choosing one.

- **Cross-lingual questions.** Some queries are written in Japanese or
  Chinese, and some answers live only in Japanese/Chinese documents. Handle
  non-English queries directly; you may answer in English or in the query's
  language.

- **Regression checks.** Some verification questions test that *unchanged*
  content is still retrievable (e.g., "what is the target pH for kombucha?"
  when only the temperature was edited). These are scored the same as any
  other question — the KB must preserve both old and new data.

- **Synthesis verification.** For entries where Agent A created a new file
  (e.g., a summary document), the verification questions test whether the KB
  ingested and can retrieve from that new file.

## When you're done

All 20 `NN-vp.md` files (one per entry, covering all verification questions
for that entry) exist under
`<knowledge-base>-files/file-update/verify-predictions/`. Hand off to the
operator for Stage 3 (scoring).
