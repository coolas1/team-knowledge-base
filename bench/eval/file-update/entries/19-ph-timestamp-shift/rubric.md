---
edit_weight: 0.50
knowledge_weight: 0.50
verification_questions:
  - 19-vq-01 (edit verification)
  - 19-vq-02 (edit verification)
edit_scoring: diff
expected_edits:
  - file: fermentation/ph-readings.csv
    change: All 30 B-018 rows (lines 62-91): each timestamp shifted -2 days. B-007 and B-013 rows unchanged.
inconsistency: false
honesty_required: false
---

# Rubric 19 — pH logger timestamp correction

## Edit scoring criteria (E)

**E1. Correctness (0–1).** Are all 30 B-018 timestamps shifted back by exactly 2 days? The first B-018 reading goes from 2024-01-05T18:00 to 2024-01-03T18:00; the last from 2024-02-03T18:00 to 2024-02-01T18:00.

**E2. Precision (0–1).** Only the B-018 rows should change. B-007 and B-013 timestamps must remain identical to the original. Any edits to non-B-018 data should be flagged as over-application. Full credit requires all 30 shifts to be correct.

## Knowledge scoring criteria (K)

**K1. VQ-01: First corrected timestamp (0–1).** Correct answer: 2024-01-03T18:00. Weight: 0.50 of knowledge score.

**K2. VQ-02: Count and date range (0–1).** Correct answer: 30 readings, from 2024-01-03 to 2024-02-01. Weight: 0.50 of knowledge score.
