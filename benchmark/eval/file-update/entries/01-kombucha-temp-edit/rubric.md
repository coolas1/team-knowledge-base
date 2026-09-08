---
edit_weight: 0.50
knowledge_weight: 0.50
verification_questions:
  - 01-vq-01 (edit verification)
  - 01-vq-02 (regression check)
edit_scoring: llm-judge
expected_edits:
  - file: fermentation/recipes.md
    change: line 24, "ferment at 22–26 °C" → "ferment at 24–28 °C"
inconsistency: false
honesty_required: false
---

# Rubric 01 — Kombucha fermentation temperature update

## Edit scoring criteria (E)

**E1. Correctness (0–1).** Was the exact edit applied to the correct file and correct line? The change is a single atomic substitution: "22–26 °C" to "24–28 °C" in the kombucha Method section of `fermentation/recipes.md`.

Partial credit (0.5) if the edit is applied correctly elsewhere in the file but not in the right location, or if the file location is wrong.

**E2. Completeness (0–1).** Was only the intended line changed? If other temperature references in the file (e.g., the Notes section mentioning "27 °C") were altered unnecessarily, deduct.

## Knowledge scoring criteria (K)

**K1. VQ-01: Temperature range (0–1).** The correct value is 24–28 °C. Exact match required. Weight: 0.50 of knowledge score.

**K2. VQ-02: pH regression (0–1).** The pH target is 3.0–3.4. This checks whether the edit introduced unintended changes to surrounding text. Weight: 0.50 of knowledge score.
