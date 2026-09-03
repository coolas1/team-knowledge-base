---
id: 06
category: CFE
difficulty: medium
edit_weight: 0.40
knowledge_weight: 0.60
edit_scoring: diff
inconsistency: false
---

# Rubric: FU-06 — Cape Hedo depth

## Edit files (2)

| # | File | Weight | Scoring |
| - | ---- | ------ | ------- |
| 1 | research/dive-sites.csv | 0.50 | diff |
| 2 | research/dive-sites-ja.csv | 0.50 | diff |

Both files must have RYK-05 typical_depth_m / 典型深度(m) changed from 14 to 16. All other rows must be unchanged.

## Verification questions (2)

| # | Question | Weight |
| - | -------- | ------ |
| 1 | Cape Hedo typical depth | 0.50 |
| 2 | Japanese name and visibility | 0.50 |

## Notes

- Only the depth value changes. Visibility, coordinates, and all other fields are unchanged.
- The agent must update both EN and JA CSVs. A single-file edit is partial credit (max 0.50 per file).
