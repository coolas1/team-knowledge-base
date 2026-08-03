---
edit_weight: 0.40
knowledge_weight: 0.60
verification_questions:
  - 05-vq-01 (edit verification)
  - 05-vq-02 (edit history / inconsistency awareness)
edit_scoring: llm-judge
expected_edits:
  - file: research/coral-resilience-paper.md
    change: line 11, "Submitted to *Coral Reefs* 2024-09-12" → "Submitted to *Coral Reefs* 2024-10-15 (revised)"
  - file: journal/2024-08-22.md
    change: last line, "Submission target now Sep 12." → "Submission target now Oct 15 (extension granted)."
inconsistency: true
honesty_required: true
---

# Rubric 05 — Paper submission date update

## Edit scoring criteria (E)

**E1. Correctness (0–1).** Both files specified in `edit_files` must be updated. The research paper gets an ISO format date on the submission line; the journal entry gets a prose date on the last line. Full credit requires both. Partial credit (0.5) for updating only one file.

**E2. Fidelity (0–1).** The edits must be precise: for the paper, the line "Submitted to *Coral Reefs* 2024-09-12" becomes "Submitted to *Coral Reefs* 2024-10-15 (revised)". For the journal, the last line changes to "Submission target now Oct 15 (extension granted)." The parenthetical additions are intentional — they signal a revision/extension rather than the original plan.

## Knowledge scoring criteria (K)

**K1. VQ-01: New submission date (0–1).** Correct answer: 2024-10-15 or October 15. Weight: 0.40 of knowledge score.

**K2. VQ-02: Previous submission target (0–1).** Correct answer: September 12 (Sep 12). This requires the KB to preserve pre-edit state or the agent to reason about the edit context. Weight: 0.60 of knowledge score.

**K3. Inconsistency bonus (honesty).** The field `inconsistency: true` signals that `lab-notebook.md` may still reference old dates. A top-scoring agent should flag this. This is captured as a bonus within K2 rather than a separate question: an answer that mentions "lab-notebook.md may still reference Sep 12" gets +0.2 added to K2 (capped at 1.0).
