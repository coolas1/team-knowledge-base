## 1. Corpus and ground truth

- [x] 1.1 Author the synthetic 48-file corpus under `benchmark/raw/` (five categories, multimodal md/csv/xlsx/pdf/images, en/ja/zh variants, cross-category references); verify every format either has an extractor or is reported as skipped by the ingest step (`23940269`).
- [x] 1.2 Write the ground-truth narrative `benchmark/eval/STORY.md` with Japanese and Chinese variants; verify every benchmark question is answerable from the narrative alone (`23940269`).

## 2. Evaluations

- [x] 2.1 Build the 40-question QA set under `benchmark/eval/qa/` with per-question prompt, reference answer, and rubric, plus separate `PREDICT_INSTRUCTIONS.md` and `EVAL_INSTRUCTIONS.md`; verify the instructions make prediction and evaluation independently repeatable (`23940269`).
- [x] 2.2 Build the four-entry file-update evaluation under `benchmark/eval/file-update/` (edit instructions, golden target files, verify-a/verify-q rubrics); verify each entry drives the edit → reindex → retrieve loop against its golden file (`23940269`).

## 3. Ingest path

- [x] 3.1 Add `benchmark/ingest.sh` (public BFF upload endpoint, category-relative titles, registry-synced extension list, reachability preflight, ok/fail/skip summary, non-zero exit on failure); verify it loads the corpus into the live 2026-09-03 app and its summary accounts for every file (`23940269`).

## 4. Live run and records

- [x] 4.1 Execute the 2026-09-03 live run: ingest the corpus through the BFF, predict and evaluate 15 of 40 questions (mean score 0.93); record per-question predictions and the aggregate under `team-knowledge-base-files/qa/` (result files committed with the preceding restructure commit `7e851d98`).
- [x] 4.2 Record the run's findings in `docs/issues.md` (relevance-gate floor below the embedding noise floor, reranker failing closed on a dead key, BFF upload allowlist diverging from the extractor registry) and update `docs/upstream-pr-followups.md`; verify each finding carries enough context to act on (`23940269`).
- [x] 4.3 Validate the change with `openspec validate retrieval-benchmark --strict` and confirm the landed commit `23940269` contains only benchmark, results-tracking, and docs files.
