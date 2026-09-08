# Evaluation runbook — scoring `<knowledge-base>` file-update results

> **Argument.** `<knowledge-base>` is the system under test. **Default `gbrain`.**
> Replace `<knowledge-base>` (and `<knowledge-base>-files`) everywhere below with
> the app you scored. Future apps reuse this runbook unchanged.

## Your job

You are the scorer. An edit agent (Agent A) modified the corpus per 20 update
prompts, then a verification agent (Agent B) answered pre-written questions
about the modified corpus. You compare both outputs against the ground truth
and assign a combined score per entry, then summarize into a report.

Be a **strict but fair** grader: partial credit is allowed; reward correctness
and honesty about gaps; penalize confident wrong or fabricated edits.

## Inputs (read these)

- `eval/file-update/README.md` — the format, the entry index, **grading
  notes**, and the **known inconsistencies**. Read this first.
- `eval/file-update/entries/NN/rubric.md` — per-entry scoring weights and
  edit criteria.
- `eval/file-update/entries/NN/verify-a/NN-va.md` — ground truth for each
  verification question.
- `eval/file-update/entries/NN/golden/` — expected final state of each
  edited file.
- `<knowledge-base>-files/file-update/edit-trails/NN-log.md` — Agent A's
  edit trail.
- `<knowledge-base>-files/file-update/edit-outputs/NN/` — Agent A's edited
  files.
- `<knowledge-base>-files/file-update/verify-predictions/NN-vp.md` — Agent
  B's verification predictions.

## How to score each entry

For each `NN`, compute two scores:

### 1. Edit correctness score (E)

For each expected edit file (listed in `rubric.md`):

- **Prose (markdown)**: LLM-judge. Compare the agent's edited file against
  the golden reference. Is the correct change applied in the right location?
  Are surrounding paragraphs unmodified?
- **Structured data (CSV)**: diff. Compare the agent's output against the
  golden reference. Data cells must match; whitespace and delimiter style
  differences are acceptable.
- **Binary (XLSX, PDF)**: if the agent honestly noted inability, cap at 0.25
  for that file. If the agent claimed to edit but the file is unchanged or
  corrupted, score 0.00. If the agent successfully edited (XLSX via openpyxl),
  score against the golden reference.
- **Synthesis (new file)**: LLM-judge against acceptance criteria in the
  golden directory. Score for coverage of required themes, correctness of
  links, and factual accuracy.

Score each file individually, then compute the weighted mean per `rubric.md`.

### 2. Knowledge retrieval score (K)

For each verification question, compare Agent B's prediction against the
verify-a answer key. Use the same scale as the QA benchmark:

| Score | Meaning |
| ----- | ------- |
| 1.00  | Fully correct, all components, well-supported by KB retrieval. |
| 0.75  | Correct core answer but missing a supporting component/citation. |
| 0.50  | Right entity but wrong/missing key detail, OR surfaced a conflict but picked the losing side without flagging it. |
| 0.25  | Substantially wrong but shows some relevant retrieval, OR an honest "not found" (retrieval miss, not fabrication). |
| 0.00  | Wrong, or fabricated/confident guess with no KB support. |

K = mean of per-question scores.

### 3. Combined score

```
Final = E × edit_weight + K × knowledge_weight
```

Weights from `rubric.md`; defaults: `edit_weight = 0.40, knowledge_weight = 0.60`.

### Special grading (from `eval/file-update/README.md`)

- **Inconsistency entries** (05, 15): full knowledge credit requires
  discovering and naming the discrepancy across sources. An answer that
  silently picks one value loses points.
- **Honesty pathway**: for binary files the agent cannot edit, an honest
  admission in the edit trail earns E=0.25 for that file (not 0.00). A
  fabricated edit claim earns 0.00.
- **Multi-modal entries** (09, 18): agents that edit only the markdown mirror
  without engaging the binary earn max 0.75 for the edit score for that file.
  The knowledge score tests whether the KB ingested the binary change.
- **Synthesis entries** (13, 14, 20): the golden directory contains acceptance
  criteria, not exact reference files. Score the created file against these
  criteria.

## Output

Write the report to:

```
<knowledge-base>-files/file-update/RESULTS.md
```

Use this shape (fill every section):

````markdown
# `<knowledge-base>` — file-update benchmark results

- **Run date:** YYYY-MM-DD
- **KB under test:** <knowledge-base>
- **Entries:** 20

## Summary

- **Overall mean score:** 0.XX / 1.00
- **Per category:**

  | Cat | Meaning                | N  | Mean |
  | --- | ---------------------- | -- | ---- |
  | TXT | Atomic text edit       | 2  | 0.XX |
  | DAT | Structured data        | 2  | 0.XX |
  | CFE | Cross-file explicit    | 2  | 0.XX |
  | CFI | Cross-file implicit    | 2  | 0.XX |
  | MOD | Multi-modal            | 2  | 0.XX |
  | XLG | Cross-language         | 2  | 0.XX |
  | SYN | Summary / synthesis    | 2  | 0.XX |
  | TMP | Temporal / numeric     | 2  | 0.XX |

- **Mean edit correctness (E):** 0.XX
- **Mean knowledge retrieval (K):** 0.XX
- **Honesty issues:** <n> entries with honest "cannot edit" admission; <n> fabricated edits.

## Per-entry scores

| ID  | Cat | E     | K     | Combined | Verdict (one line) |
| --- | --- | ----- | ----- | -------- | ------------------ |
| 01  | TXT | 1.00  | 1.00  | 1.00     | Clean: temp updated, KB retrieved correct value. |
| …   |     |       |       |          |                    |

## Per-entry detail

### FU-01 — Kombucha temperature edit
- **Edit score:** 1.00 (recipes.md: 22–26 → 24–28 °C on correct line)
- **Knowledge score:** 1.00 (VQ-01: "24–28 °C"; VQ-02: "3.0–3.4")
- **Combined:** 1.00 (0.50 × 1.00 + 0.50 × 1.00)
- **Verdict:** Clean edit, KB re-ingested, Agent B retrieved both new and unchanged values.

(repeat for 01–20)

## Notable findings

- <Edits the agent could/could not apply.>
- <Binary files the agent edited via programmatic access vs. admitted inability.>
- <Cross-file consistency: which multi-file edits stayed consistent.>
- <Cross-language handling quality.>
- <Honesty patterns: which formats caused trouble.>
- <Recurring failure patterns and strengths.>
````

## Ground rules

- Score from the answer key, golden files, and README. The score reflects
  what Agents A and B produced, so you generally need not re-query the KB or
  read the temp copy.
- If an edit trail or prediction file is missing for an entry, score the
  corresponding dimension as 0.00 and note "missing."
- Be consistent: similar mistakes earn similar partial scores across entries.
