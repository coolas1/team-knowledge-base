---
edit_weight: 0.50
knowledge_weight: 0.50
verification_questions:
  - 04-vq-01 (edit verification)
  - 04-vq-02 (regression check)
edit_scoring: diff
expected_edits:
  - file: research/dive-sites.csv
    change: RYK-03 row, visibility_m column — 23 → 17
inconsistency: false
honesty_required: false
---

# Rubric 04 — Dive site visibility correction

## Edit scoring criteria (E)

**E1. Correctness (0–1).** Was the visibility value for site RYK-03 (Kabira North) changed from 23 to 17 in the correct column (visibility_m)?

**E2. Precision (0–1).** Only the visibility_m field for RYK-03 should change. Any edits to other rows, columns, or other sites should be flagged as over-application.

## Knowledge scoring criteria (K)

**K1. VQ-01: Revised visibility (0–1).** Correct answer: 17 m. Weight: 0.50 of knowledge score.

**K2. VQ-02: Depth regression (0–1).** Correct answer: 8 m (unchanged). This verifies that the edit did not spill over to adjacent fields. Weight: 0.50 of knowledge score.
