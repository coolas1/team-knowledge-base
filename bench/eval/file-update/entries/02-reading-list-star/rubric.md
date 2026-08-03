---
edit_weight: 0.50
knowledge_weight: 0.50
verification_questions:
  - 02-vq-01 (edit verification)
  - 02-vq-02 (edit history)
edit_scoring: llm-judge
expected_edits:
  - file: notes/reading-list.md
    change: Move Powers (2018) entry from "Backlog" section to "Recently finished" section; prefix with star; update note to "Finished -- incredible"
inconsistency: false
honesty_required: false
---

# Rubric 02 — Reading list recommendation

## Edit scoring criteria (E)

**E1. Correctness (0–1).** Was the correct entry (Powers, *The Overstory*) updated? The original line "- Powers (2018), *The Overstory*. Started twice, stopped twice." under "Backlog (not yet started)" must be moved to "Recently finished" and changed to "- **\*** Powers (2018), *The Overstory*. Finished -- incredible."

**E2. Completeness (0–1).** The edit requires two actions: (1) add the star indicator, (2) move to the correct section. Deduct if only one is done, or if the wrong entry is modified.

## Knowledge scoring criteria (K)

**K1. VQ-01: Recommendation status (0–1).** The answer should confirm the book is now starred/recommended. Weight: 0.50 of knowledge score.

**K2. VQ-02: Previous location (0–1).** The answer should surface that the entry was previously in the Backlog section. This tests whether the KB preserves pre-edit state. Weight: 0.50 of knowledge score.
