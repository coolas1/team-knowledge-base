---
edit_weight: 0.50
knowledge_weight: 0.50
verification_questions:
  - 17-vq-01 (edit verification)
  - 17-vq-02 (edit verification)
edit_scoring: diff
expected_edits:
  - file: research/dive-sites.csv
    change: Append row RYK-13,Yoron South,Yoronjima,27.044,128.401,13,30,intermediate,SW,forereef
  - file: research/dive-sites-ja.csv
    change: Append row RYK-13,与論南,与論島,27.044,128.401,13,30,中級,南西,外礁
inconsistency: false
honesty_required: false
---

# Rubric 17 — New dive site: Yoron South

## Edit scoring criteria (E)

**E1. Correctness (0–1).** Was RYK-13 appended to both CSV files (English and Japanese) with the correct values? Full credit only if both files are updated.

**E2. Precision (0–1).** Only a single row should be appended to each file. Existing rows must remain unchanged. Partial credit (0.5) if only one file is updated.

## Knowledge scoring criteria (K)

**K1. VQ-01: Latitude and visibility of Yoron South (0–1).** Correct answer: latitude 27.044, visibility 30 m. Weight: 0.50 of knowledge score.

**K2. VQ-02: Japanese name and island group (0–1).** Correct answer: 与論南 (Yoron South), 与論島 (Yoronjima). Weight: 0.50 of knowledge score.
