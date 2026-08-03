# Evaluation runbook — scoring `<knowledge-base>` predictions

> **Argument.** `<knowledge-base>` is the system under test. **Default `gbrain`.**
> Replace `<knowledge-base>` (and `<knowledge-base>-files`) everywhere below with
> the app you scored. Future apps reuse this runbook unchanged.

## Your job

You are the scorer. A prediction agent answered the 40 retrieval questions using
*only* the `<knowledge-base>` (per `PREDICT_INSTRUCTIONS.md`). You compare each
prediction against the ground-truth answer key, assign a 0–1 score, then
summarize into a report. Be a **strict but fair** grader: partial credit is
allowed; reward correctness and honesty about gaps; penalize confident wrong or
fabricated answers.

## Inputs (read these)

- `eval/qa/README.md` — the format, the question index, **grading notes**, and
  the **known corpus inconsistencies**. Read this first; it defines how the
  flagged questions must be graded.
- `eval/qa/answers/NN-a.md` — the ground truth for question `NN`. Each has an
  **Answer**, **Sources**, **Reasoning**, **Gotchas**, and **Acceptable answer
  variants** section.
- `<knowledge-base>-files/qa/predictions/NN-p.md` — the prediction to score.

## How to score each question

For each `NN`, compare the prediction's `## Answer` against the answer key:

1. **Correctness** — does it match a value listed under *Acceptable answer
   variants* (or equivalent)? Are all required components present?
2. **Supporting evidence** — does the retrieval trail justify the answer, and
   does it engage the right modality (see below)?
3. **Honesty** — a prediction that says "not found in KB" when the answer *is*
   in the corpus is a retrieval miss (low but non-zero, for honesty) and far
   better than a fabricated answer (0.0).

### Score scale (0.00–1.00)

| Score | Meaning |
| ----- | ------- |
| 1.00  | Fully correct, all components, well-supported, right modality. |
| 0.75  | Correct core answer but missing a supporting component/citation, or text-only derivation for a multi-modal question. |
| 0.50  | Right entity but wrong/missing key detail, OR surfaced a conflict but picked the losing side without flagging it. |
| 0.25  | Substantially wrong but shows some relevant retrieval, OR an honest "not found" (retrieval miss, not fabrication). |
| 0.00  | Wrong, or a fabricated/confident guess with no KB support. |

Interpolate between anchors as needed; record to two decimals.

### Special grading (from `eval/qa/README.md`)

- **Inconsistency questions** — per the README's "Known corpus inconsistencies"
  and any A-file *Gotcha* that flags one (e.g. 09, 20, 28, 29, 39): **full credit
  requires discovering and naming the discrepancy across sources.** A prediction
  that silently picks one value loses points even if that value is individually
  "right."
- **Multi-modal questions (22–30, 34, 40)** — a text-only answer derived from a
  Markdown mirror, without engaging the actual binary through the KB, is at most
  ~0.75 even if the value is correct (the KB failed its multi-modal job).
- **Cross-lingual questions (31–40)** — answers may be in English or in the
  query's/source's language; accept either. Credit a prediction that cites the
  correct JA/ZH source file. For Q39 (an EN-vs-JA inconsistency), apply the
  inconsistency rule across the two languages.
- **Single precise answer** — each question has one right answer; expression may
  vary per *Acceptable answer variants*.

## Output

Write the report to:

```
<knowledge-base>-files/qa/RESULTS.md
```

Use this shape (fill every section):

````markdown
# `<knowledge-base>` — retrieval benchmark results

- **Run date:** YYYY-MM-DD
- **KB under test:** <knowledge-base>
- **Questions:** 40

## Summary

- **Overall mean score:** 0.XX / 1.00
- **Per category:**

  | Cat | Meaning              | N  | Mean |
  | --- | -------------------- | -- | ---- |
  | D   | Detail locating      | 11 | 0.XX |
  | L   | Link following       | 10 | 0.XX |
  | T   | Temporal / date range| 8  | 0.XX |
  | M   | Multi-modal          | 11 | 0.XX |
  | X   | Cross-lingual (31–40)| 10 | 0.XX |

- **Honesty:** <n> honest "not found" vs <n> fabricated/unsupported answers.

## Per-question scores

| ID | Cat | Score | Verdict (one line) |
| -- | --- | ----- | ------------------ |
| 01 | D   | 1.00  | Correct: HOBO U22-001, 8 m, 30 min, from PDF §2.2. |
| 02 | D   | 0.00  | Fabricated track name; KB had nothing. |
| …  |     |       | |

## Per-question detail

### Q01 — <title>
- **Score:** 1.00
- **Ground truth:** …
- **Prediction:** …
- **Verdict:** …

(repeat for 01–40)

## Notable findings

- <Inconsistencies the KB did / did not surface.>
- <Multi-modal files the KB parsed vs. missed.>
- <Recurring failure patterns and strengths.>
````

## Ground rules

- Score from the answer key and README. The score reflects what the prediction
  agent retrieved, so you generally need not re-query the KB or read `raw/`
  (that would re-litigate the prediction).
- If a prediction file is missing for a question, score it 0.00 and note
  "missing prediction."
- Be consistent: similar mistakes earn similar partial scores across questions.
