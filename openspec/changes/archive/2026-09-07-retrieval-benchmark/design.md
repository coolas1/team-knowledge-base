## Context

The 2026-09-03 live run needed to verify three things at once: that parallel ingest left retrieval quality unchanged, that PR #3's recall relevance gate behaved as intended, and that PR #4's indexed-file edit flow worked end to end. None of that could be measured without a corpus, a question set, and rubrics — and there was no convention for where a run's results or findings should live. See proposal.md - Why for the motivation.

## Goals / Non-Goals

**Goals:**
- A fixed corpus with complete ground truth, so scores are attributable to the app and comparable across runs.
- Cover every extractor (markdown, CSV, XLSX, PDF, images/OCR) and every language the team works in (en/ja/zh).
- Exercise the deployed path (public BFF endpoint), not internal APIs.

**Non-Goals:**
- No automated scoring pipeline — prediction and evaluation are instruction-driven and (for now) human/LLM-executed; automation is a future change.
- No scale testing — 48 files measures retrieval quality, not ingest throughput.
- No CI integration; runs are deliberate events with recorded results.

## Decisions

### Synthetic corpus around a fictional persona
48 files in five categories (journal, music, notes, research, travel) centered on a fictional marine-researcher/musician persona.
- Why: no real personal data (shareable, committable); full control over ground truth; five categories with deliberate cross-references (a research site mentioned in travel notes, tracks named in journal entries) give the entity/relation graph something to connect.
- Alternative: real team documents — rejected: privacy and no authoritative ground truth. Public datasets — rejected: no cross-document structure.

### Multimodal and multilingual by design
Formats chosen to cover the extractor registry (md/csv/xlsx/pdf/images); ja/zh variants of key documents.
- Why: the benchmark must catch extractor regressions (image OCR, PDF, XLSX) and multilingual retrieval gaps, not just markdown search.
- Consequence surfaced by the first run: the BFF upload allowlist diverged from the extractor registry (`csv` rejected by the BFF, `xlsx` unextractable) — exactly the class of bug the corpus exists to find; recorded in `docs/issues.md`.

### Story as the single ground truth
`STORY.md` (plus ja/zh variants) narrates everything the corpus contains.
- Why: grading needs a truth source independent of the app; the story is cheaper to maintain than per-file metadata and doubles as the corpus authoring guide.

### Rubric-scored questions with separated predict/eval phases
40 questions, each with prompt, reference answer, and rubric; `PREDICT_INSTRUCTIONS.md` and `EVAL_INSTRUCTIONS.md` are separate documents.
- Why: separating answer generation from grading makes a run repeatable and lets a different evaluator re-score existing predictions; rubrics (rather than exact-match) tolerate legitimate answer phrasing.

### File-update entries with golden files
Four entries (kombucha temperature edit, reading-list update, tracklist addition, dive-visibility data), each with edit instructions, a golden target file, and two verification passes (verify-a against the app, verify-q against questions).
- Why: PR #4's indexed-file edit flow needed a systematic test of edit → reindex → retrieve; golden files make the expected outcome unambiguous.

### Ingest through the public BFF endpoint
`benchmark/ingest.sh` uploads each file as multipart to `POST /api/documents/upload`, using the category-relative path as the stored title, syncing its extension list with the extractor registry, preflighting `/openapi.json`, and summarizing ok/fail/skip.
- Why: the benchmark must measure the path users actually hit; relative-path titles keep categories visible and names unique.
- Alternative: CLI ingest — rejected: bypasses the BFF (validation, upload errors) that the live run was verifying.

### Results live outside the code tree
Per-question predictions and aggregate results go under `team-knowledge-base-files/qa/`, findings into `docs/issues.md`.
- Why: predictions are run artifacts, not source; the issue docs are the durable tracker.

## Risks / Trade-offs

- [15 of 40 questions were scored in the first run (time-boxed)] → the unrun questions remain available; recorded results clearly state coverage.
- [Manual/LLM grading is slower and less uniform than automated metrics] → rubrics constrain judgment; automation can be added later without changing the corpus or questions.
- [Corpus changes invalidate historical comparisons] → that is intended; corpus edits are versioned events.

## Migration Plan

Already applied on `main` (`23940269`; the run's result files were committed with the preceding restructure commit `7e851d98`). Purely additive content — no production code changed. Future runs re-execute the instructions against the corpus.

## Open Questions

- Whether and how to automate scoring (e.g. an LLM evaluator applying the rubrics) — deferrable; the instructions-first approach works today.
