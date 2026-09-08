---
edit_weight: 0.50
knowledge_weight: 0.50
verification_questions:
  - 03-vq-01 (edit verification)
  - 03-vq-02 (metadata regression)
edit_scoring: diff
expected_edits:
  - file: music/tracklist.csv
    change: Append row "7,Thermocline,74,G minor,04:30,Sesoko West deep hydrophone,deluxe edition bonus track"
inconsistency: false
honesty_required: false
---

# Rubric 03 — Tracklist addition

## Edit scoring criteria (E)

**E1. Correctness (0–1).** Was the exact new row appended to the CSV? Expected row: `7,Thermocline,74,G minor,04:30,Sesoko West deep hydrophone,deluxe edition bonus track`. Fields must match exactly, and the row must be appended as a new row, not inserted mid-file.

**E2. Completeness (0–1).** All fields must match: track_no=7, title=Thermocline, bpm=74, key=G minor, length=04:30, primary_samples=Sesoko West deep hydrophone, notes=deluxe edition bonus track. Deduct for missing or incorrect fields.

## Knowledge scoring criteria (K)

**K1. VQ-01: New track details (0–1).** The answer should correctly state title "Thermocline" and BPM "74". Weight: 0.50 of knowledge score.

**K2. VQ-02: Track count (0–1).** Correct count is 7 (was 6). Weight: 0.50 of knowledge score.
