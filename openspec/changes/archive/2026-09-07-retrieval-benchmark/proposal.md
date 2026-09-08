## Why

Retrieval quality had no repeatable measurement. The parallel-ingest acceleration needed proof that faster ingestion did not change what gets stored or found, the recall relevance gate (PR #3) needed calibration evidence, and every future retrieval change needs a regression baseline. The 2026-09-03 live run had to be driven by a fixed corpus, a fixed question set, and scoring rubrics — before this change, verification was ad hoc and its findings lived nowhere.

## What Changes

- Add a fixed synthetic corpus under `benchmark/raw/`: 48 files across five categories (journal, music, notes, research, travel) following the fictional "Wren Adachi" persona — multimodal (markdown, CSV, XLSX, PDF, images) and multilingual (English, Japanese, Chinese), with cross-category references that exercise the entity/relation graph. No real personal data.
- Add the corpus ground truth under `benchmark/eval/`: the full narrative (`STORY.md`) plus Japanese and Chinese variants, sufficient to grade answers without the app.
- Add a 40-question QA benchmark (`benchmark/eval/qa/`): per-question prompts, reference answers, and scoring rubrics, plus separate predict and evaluation instructions so runs are repeatable and comparable.
- Add a 4-entry file-update evaluation (`benchmark/eval/file-update/`): edit instructions, golden target files, and verification questions/rubrics per entry, exercising the indexed-file edit → reindex → retrieve loop.
- Add `benchmark/ingest.sh`: bulk-ingest the corpus through the public BFF upload endpoint (per-file multipart upload, category-relative titles, extractor-extension sync, reachability preflight, per-file ok/fail/skip summary, non-zero exit on failure).
- Record the scored results of the 2026-09-03 live run (15 of 40 questions run, mean score 0.93) under `team-knowledge-base-files/qa/`, and the run's findings in `docs/issues.md` (relevance-gate floor below the embedding noise floor, reranker failing closed on a dead key, upload allowlist diverging from the extractor registry) and `docs/upstream-pr-followups.md`.

## Capabilities

### New Capabilities
- `retrieval-benchmark`: A repeatable retrieval-quality measurement — fixed synthetic multimodal/multilingual corpus, scored QA question set, file-update evaluation, end-to-end ingest through the public API, and per-run recorded results.

### Modified Capabilities

None.

## Impact

- `benchmark/raw/` — the synthetic corpus (48 files; commit adds them all).
- `benchmark/eval/` — ground-truth narrative, QA question set with rubrics, file-update entries with golden files, predict/eval instructions.
- `benchmark/ingest.sh` — corpus ingest script against the BFF upload endpoint.
- `team-knowledge-base-files/qa/` — per-question predictions and aggregate results from the live run.
- `docs/issues.md`, `docs/upstream-pr-followups.md` — live-run findings and follow-up tracking.
- No production code changes.
- Retroactive note: this change documents work already applied to `main` as commit `23940269` ("test(benchmark): add retrieval benchmark and eval docs"). Git-history quirk: the scored result files under `team-knowledge-base-files/qa/` were committed slightly earlier, swept into the restructure commit `7e851d98`; the benchmark commit carries the harness that produced them.
