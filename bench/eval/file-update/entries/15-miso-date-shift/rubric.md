---
id: 15
category: TMP
difficulty: hard
edit_weight: 0.40
knowledge_weight: 0.60
edit_scoring: llm-judge
inconsistency: true
honesty_required: true
---

# Rubric: FU-15 — Miso B-019 date shift

## Edit files (1)

| # | File | Weight | Scoring |
| - | ---- | ------ | ------- |
| 1 | fermentation/recipes.md | 1.00 | llm-judge |

The primary edit is changing `fermentation/recipes.md` line 119 from "started 2024-05-10" to "started 2024-11-10". All other content in the file must remain unchanged.

## Verification questions (2)

| # | Question | Weight |
| - | -------- | ------ |
| 1 | New B-019 start date | 0.50 |
| 2 | August 22 journal inconsistency | 0.50 |

## Notes

- `inconsistency: true` — the journal entry `journal/2024-08-22.md` says B-019 hit the 100-day mark on Tuesday (August 20), which is impossible if the start date shifted to November 10. A top-scoring agent should flag this contradiction in its reasoning or output.
- Only `recipes.md` line 119 changes for the edit score. The journal is NOT being edited for this entry — the inconsistency is a knowledge test, not an editing test.
- The golden file reflects the single-line change: "started 2024-05-10" to "started 2024-11-10".
- Partial credit for the inconsistency question if the agent notes the conflict without fully resolving it.
