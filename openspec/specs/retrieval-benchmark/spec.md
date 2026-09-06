# retrieval-benchmark Specification

## Purpose

Defines the project's repeatable retrieval-quality measurement: a fixed synthetic corpus, a scored QA question set, a file-update evaluation, an end-to-end ingest path through the public API, and per-run recorded results that future retrieval changes can be measured against.

## Requirements

### Requirement: Fixed synthetic multimodal corpus
The benchmark SHALL provide a fixed, versioned corpus of synthetic documents containing no real personal data. The corpus SHALL span multiple document formats (markdown, CSV, XLSX, PDF, images), multiple languages (English, Japanese, Chinese), thematic categories, and cross-category references that exercise entity/relation extraction. Changes to corpus contents SHALL be treated as versioned events that invalidate prior scores.

#### Scenario: Corpus is stable across runs
- **WHEN** two benchmark runs ingest the corpus
- **THEN** both ingest the same set of files with the same contents, so scores are comparable

#### Scenario: Multimodal coverage
- **WHEN** the corpus is ingested
- **THEN** every format present has a registered extractor or is explicitly reported as skipped by the ingest step

### Requirement: Ground-truth narrative
The benchmark SHALL provide a ground-truth narrative of the corpus, in English and in the corpus's other languages, from which every benchmark question can be answered and graded without access to the application.

#### Scenario: Grading without the app
- **WHEN** a scorer grades benchmark answers
- **THEN** the narrative alone provides sufficient truth to apply every question's rubric

### Requirement: Scored QA question set
The benchmark SHALL provide a fixed QA question set with one prompt, reference answer, and scoring rubric per question, together with separate prediction and evaluation instructions. Prediction (generating answers through the app) SHALL be separable from evaluation (applying rubrics to predictions), so that runs are repeatable and results comparable across changes.

#### Scenario: Two runs are comparable
- **WHEN** the same app build answers the question set twice
- **THEN** per-question scores are comparable, and differences trace to the app, not to procedure

#### Scenario: Rubric application
- **WHEN** an answer is evaluated
- **THEN** the question's rubric yields a score without requiring evaluator judgment beyond its stated criteria

### Requirement: File-update evaluation
The benchmark SHALL provide a file-update evaluation: per entry, edit instructions, a golden target file, and verification questions with rubrics. Each entry SHALL exercise the indexed-file edit flow — editing stored content, reindexing, and retrieving the updated content.

#### Scenario: An edit entry is executed
- **WHEN** an entry's edit is applied through the app and its verification questions are answered
- **THEN** the stored document matches the golden file and the updated content is retrievable

### Requirement: End-to-end ingest through the public API
The benchmark's ingest step SHALL load the corpus through the application's public upload endpoint, preserving category-relative document titles, skipping file types without an extractor, checking application reachability before uploading, and reporting per-file success/failure/skip with a non-zero exit code when any upload fails.

#### Scenario: Corpus ingest against a live app
- **WHEN** the ingest step runs with the app reachable
- **THEN** it reports per-file ok/fail/skip counts and exits non-zero if any supported file failed

#### Scenario: App unreachable
- **WHEN** the ingest step runs with the app unreachable
- **THEN** it fails fast with a clear message before attempting uploads

#### Scenario: Unsupported file type
- **WHEN** the corpus contains a file type without an extractor
- **THEN** the ingest step reports it as skipped rather than failed

### Requirement: Results are recorded per run
Each benchmark run SHALL record its per-question predictions and aggregate scores in a results directory, and material findings SHALL be tracked in the project's issue documentation.

#### Scenario: Live-run results are recorded
- **WHEN** a benchmark run completes
- **THEN** its predictions and aggregate score are stored alongside the benchmark artifacts and referenced by the issue documentation

#### Scenario: Findings drive follow-up
- **WHEN** a run surfaces a defect (e.g. a scoring-gate miscalibration)
- **THEN** it is recorded in the issue documentation with enough context to act on
