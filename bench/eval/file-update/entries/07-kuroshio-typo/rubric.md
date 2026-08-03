---
id: 07
category: CFI
difficulty: medium
edit_weight: 0.40
knowledge_weight: 0.60
edit_scoring: llm-judge
inconsistency: false
---

# Rubric: FU-07 — Kuroshio typo fix

## Edit files (1)

| # | File | Weight | Scoring |
| - | ---- | ------ | ------- |
| 1 | music/field-recordings-okinawa.md | 1.00 | llm-judge |

All occurrences of "Kuroshiro" must be changed to "Kuroshio". The agent must identify the file containing the typo through search (prompt does not name the file). Expected: occurrences fixed across the file.

## Verification questions (2)

| # | Question | Weight |
| - | -------- | ------ |
| 1 | Correct track name | 0.50 |
| 2 | Recording source | 0.50 |

## Notes

- The agent must discover which file contains the typo by searching the corpus.
- tracklist.csv contains the correct spelling — the agent can use it as reference for the correct name.
- Adjacent content must not be modified.
