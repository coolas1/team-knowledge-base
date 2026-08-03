---
id: 11
category: XLG
difficulty: medium
edit_weight: 0.50
knowledge_weight: 0.50
edit_scoring: diff
inconsistency: false
---

# Rubric: FU-11 — 泡盛バッチ記録の追加 (Awamori batch record addition)

## Edit files (1)

| # | File | Weight | Scoring |
| - | ---- | ------ | ------- |
| 1 | fermentation/awamori-batch-ja.csv | 1.00 | diff |

Append a new row for batch A-005 with the specified fields. The CSV header must not change.

## Verification questions (2)

| # | Question | Weight |
| - | -------- | ------ |
| 1 | A-005 start date and final gravity (JA) | 0.50 |
| 2 | Batch count and highest ABV (EN) | 0.50 |

## Notes

- The new row must exactly match: `A-005,泡盛（米・黒麹）,2025-01-15,15,甕（かめ）,31,1.050,0.986,42.5,4,新規農家の米を使用`
- Only the new row should be added. Existing rows must not be modified.
- The edit is a single atomic append at the end of the file.
