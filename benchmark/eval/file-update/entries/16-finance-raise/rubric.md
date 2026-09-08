---
edit_weight: 0.50
knowledge_weight: 0.50
verification_questions:
  - 16-vq-01 (edit verification)
  - 16-vq-02 (edit verification)
edit_scoring: diff
expected_edits:
  - file: notes/finance-2024.csv
    change: Jul row, income_usd 4857->5200, savings_usd 1366->1709; Aug row, income_usd 5343->5600, savings_usd 1640->1897
inconsistency: false
honesty_required: false
---

# Rubric 16 — Finance records raise update

## Edit scoring criteria (E)

**E1. Correctness (0–1).** Were the correct rows (Jul, Aug) updated with the correct income values (5200, 5600) and recalculated savings (1709, 1897)?

**E2. Precision (0–1).** Only the Jul and Aug rows should change. All other months must remain identical to the original. Any edits to other months or columns should be flagged as over-application.

## Knowledge scoring criteria (K)

**K1. VQ-01: July savings after raise (0–1).** Correct answer: 1709 USD. Weight: 0.50 of knowledge score.

**K2. VQ-02: August income (0–1).** Correct answer: 5600 USD. Weight: 0.50 of knowledge score.
