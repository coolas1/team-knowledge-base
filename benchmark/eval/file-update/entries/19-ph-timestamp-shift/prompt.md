---
id: 19
category: TMP (tabular)
difficulty: hard
edit_modality: single-file
edit_files:
  - fermentation/ph-readings.csv
target_apps:
  - gbrain
  - WeKnora
  - team-knowledge-base
---

# Prompt 19 — pH logger timestamp correction

I just discovered my pH logger's clock was off by exactly 2 days forward for the B-018 readings. All B-018 timestamps in the pH readings file need to be shifted back by 2 days. For example, if a reading shows 2024-01-05, it should become 2024-01-03. The B-007 and B-013 readings are fine — only fix B-018.
