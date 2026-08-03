---
edit_weight: 0.30
knowledge_weight: 0.70
verification_questions:
  - 20-vq-01 (edit verification)
  - 20-vq-02 (knowledge verification)
edit_scoring: llm-judge
expected_edits:
  - file: notes/reading-cross-refs.md
    change: New file listing books referenced across 2+ project areas
inconsistency: false
honesty_required: false
---

# Rubric 20 — Reading cross-references

## Edit scoring criteria (E)

**E1. Completeness (0–1).** Does the file identify at least 3 books that appear in 2+ project areas? Must include Schafer (1977) *The Soundscape* (music + ideas), Katz (2012) *The Art of Fermentation* (fermentation + ideas), and Strogatz (2003) *Sync* (music + ideas).

**E2. Quality (0–1).** For each cross-referenced book, does the file link to the source files and describe how the book connects to each project area? The analysis should demonstrate synthesis across documents, not just a raw list.

## Knowledge scoring criteria (K)

**K1. VQ-01: Schafer book (0–1).** Correct answer: *The Soundscape* (1977). Must identify the specific book title. Weight: 0.50 of knowledge score.

**K2. VQ-02: Number of cross-project books (0–1).** Correct answer: At least 3. The answer should mention the count or name at least three books. Weight: 0.50 of knowledge score.
