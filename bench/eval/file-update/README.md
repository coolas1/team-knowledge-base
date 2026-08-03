# File-update benchmark — 20 entries

## Purpose

Evaluate PKM, RAG, and AI agents (currently gbrain, WeKnora, and
team-knowledge-base) on their ability to **edit** files and propagate those
edits to the knowledge
base — then verify that a fresh agent instance retrieves the updated
information correctly.

Each entry tests two dimensions:

1. **Edit correctness**: did the agent modify the right file(s) with the
   right changes?
2. **Knowledge update**: does a fresh agent, pointed at the modified corpus,
   correctly retrieve the new (not old) information?

The corpus is a copy of `raw/` (the same "Wren Adachi" personal archive used
by the QA benchmark), so every edit is grounded in real multi-format,
multi-language content.

## Format families exercised

| Format   | Editable? | Tested in entries                                        |
| -------- | --------- | -------------------------------------------------------- |
| Markdown | Yes       | All entries                                              |
| CSV      | Yes       | 03, 04, 06, 10, 11, 16, 17, 19                          |
| XLSX     | Depends*  | 09 (honesty pathway if not editable)                     |
| PDF      | No**      | 18 (honesty pathway — agent must admit inability)        |
| PNG      | No**      | (covered implicitly via markdown mirror edits)           |

\* XLSX can be edited programmatically (openpyxl); agents without library
access should note inability honestly.
\** PDF/PNG are generated binaries. Agents cannot edit them without the
generator script. Honest admission gets partial credit (0.25); fabrication
gets 0.00.

## Test procedure (3 stages)

1. **EDIT (Agent A)**: copy `raw/` → read the update prompt → find and edit
   the correct file(s) → re-ingest into the KB if the app requires it.
2. **VERIFY (Agent B)**: a **fresh** agent with no memory of Stage 1 answers
   pre-written verification questions using the updated KB/corpus.
3. **SCORE (Eval Agent)**: compare edits against golden references (diff for
   CSV, LLM-judge for prose) and verification predictions against answer keys.

Full runbooks:
- [`EDIT_INSTRUCTIONS.md`](EDIT_INSTRUCTIONS.md) — Agent A (Stage 1: edit the corpus)
- [`VERIFY_INSTRUCTIONS.md`](VERIFY_INSTRUCTIONS.md) — Agent B (Stage 2: answer verification questions)
- [`EVAL_INSTRUCTIONS.md`](EVAL_INSTRUCTIONS.md) — Scorer (Stage 3: score edits + predictions)

## Categories

| Code | Meaning                  | Count | What it stress-tests                                |
| ---- | ------------------------ | ----- | --------------------------------------------------- |
| TXT  | Atomic text edit         | 2     | Single-value change in one markdown file            |
| DAT  | Structured data edit     | 2     | Modify a CSV/XLSX row or cell                       |
| CFE  | Cross-file (explicit)    | 2     | Prompt names the files to update; agent chains them |
| CFI  | Cross-file (implicit)    | 2     | Vague prompt; agent must discover connected files   |
| MOD  | Multi-modal edit         | 2     | Change in binary + markdown mirror consistency      |
| XLG  | Cross-language edit      | 2     | Edit JA/ZH content, or JA/ZH prompt                 |
| SYN  | Summary / synthesis      | 2     | Read N files, create new summary, link it           |
| TMP  | Temporal / numeric       | 2     | Dates, numbers, date arithmetic                     |

Plus 4 edge-case entries that combine categories or test special scenarios
(paired CSV edit, PDF honesty, bulk timestamp shift, cross-domain synthesis).
**Total: 20 entries.**

## File layout

```
eval/file-update/
├── README.md                  ← this file (format, index, grading rules)
├── EDIT_INSTRUCTIONS.md       ← runbook for Agent A (Stage 1: edit)
├── VERIFY_INSTRUCTIONS.md     ← runbook for Agent B (Stage 2: verify)
├── EVAL_INSTRUCTIONS.md       ← runbook for scoring agents (Stage 3)
└── entries/
    NN-short-descriptor/
      ├── prompt.md            ← update instruction for Agent A
      ├── verify-q/
      │    01-vq.md, 02-vq.md  ← verification questions for Agent B
      ├── verify-a/
      │    01-va.md, 02-va.md  ← ground-truth answers
      ├── golden/              ← expected final state of each changed file
      └── rubric.md            ← per-entry scoring metadata
```

IDs are zero-padded 01–20.

Per-app output is written outside this directory, under
`<appname>-files/file-update/`: `edit-trails/NN-log.md` (Agent A's reasoning),
`edit-outputs/NN/` (edited files for scoring), `verify-predictions/NN-vp.md`
(Agent B's answers), and `RESULTS.md` (the scored report).

## Entry index

| ID  | Cat | Summary                                                          | Inconsistency? |
| --- | --- | ---------------------------------------------------------------- | -------------- |
| 01  | TXT | Kombucha temp range: 22–26 → 24–28 °C                            |                |
| 02  | TXT | Star "The Overstory" as recommended, add note                    |                |
| 03  | DAT | Add track 7 "Thermocline" to tracklist.csv                       |                |
| 04  | DAT | Update RYK-03 visibility: 23 → 17 m                              |                |
| 05  | CFE | Paper submission date: Sep 12 → Oct 15 (paper + journal)         | ✓ (old date in lab notebook) |
| 06  | CFE | Cape Hedo depth: 14 → 16 m in EN + JA CSVs                      |                |
| 07  | CFI | Fix "Kuroshiro" → "Kuroshio" typo (agent must find the file)     |                |
| 08  | CFI | Wren joins Univ. of Tokyo — update affiliation everywhere        |                |
| 09  | MOD | RYK-06 Q2 bleaching: 15% → 18% in xlsx                           |                |
| 10  | MOD | Add expense row + update blog JPY total                          |                |
| 11  | XLG | Add awamori batch A-005 (JA prompt, JA CSV)                      |                |
| 12  | XLG | Add "kelp" to trilingual glossary (EN/JA/ZH)                     |                |
| 13  | SYN | Create 2024 year-in-review from journal entries                  |                |
| 14  | SYN | Create fermentation FAQ from all fermentation files              |                |
| 15  | TMP | Shift B-019 miso dates +6 months (3 files)                       | ✓ (old dates remain in other refs) |
| 16  | TMP | Update Jul–Aug income + recalculate savings                      |                |
| 17  | DAT | Add RYK-13 Yoron South to EN + JA dive-sites CSVs                |                |
| 18  | MOD | Add "dramatically" to paper abstract — PDF uneditable (honesty)  |                |
| 19  | TMP | Shift B-018 pH timestamps −2 days (30 rows)                      |                |
| 20  | SYN | Create cross-reading-reference file from books × projects        |                |

## Scoring

### Edit correctness (E) — per entry

| Score | Prose Edit (LLM-judge)           | Structured Data (diff)              |
| ----- | -------------------------------- | ----------------------------------- |
| 1.00  | Exactly correct change, right location | File matches golden reference  |
| 0.75  | Core correct, formatting differs | Only format diffs; data correct     |
| 0.50  | Partially correct or wrong location | Data matches in some rows/cols   |
| 0.25  | Vague indication, no actual edit | Slightly related edit (wrong row)   |
| 0.00  | No edit, or edit to wrong file   | No edit, or file deleted/corrupted  |

For multi-file entries: E = weighted mean of per-file scores (equal weight
unless rubric.md overrides).

### Knowledge retrieval (K) — per verification question

Same 0.00/0.25/0.50/0.75/1.00 scale as the QA benchmark. For entries with
multiple verification questions: K = mean of per-question scores.

### Combined score

```
Final = E × edit_weight + K × knowledge_weight
```

Default: `edit_weight = 0.40, knowledge_weight = 0.60`. Overrides defined
per-entry in `rubric.md`.

### Grading notes

- **Honesty pathway.** If an agent cannot edit a binary file (PDF, XLSX) and
  reports this explicitly, the edit score for that file is capped at 0.25
  (not 0.00). A fabricated edit claim gets 0.00.
- **Inconsistency handling.** For entries flagged above (05, 15), full credit
  requires surfacing discrepancies between edited and unchanged files — not
  silently picking one value.
- **Multi-modal editing.** For entries 09 and 18, agents that edit only the
  markdown mirror without engaging the binary earn partial credit (max 0.75
  for edit score). The knowledge score tests whether the KB ingested the
  binary change.
- **Synthesis scoring.** For entries 13, 14, 20: the golden directory
  contains acceptance criteria rather than exact reference files. LLM-judge
  evaluates coverage, link quality, and accuracy.

## Regenerating / extending

- All entries are hand-written against the existing `raw/` corpus content.
- To add a new entry: pick the next free ID, create the directory following
  the template at any existing entry, and add a row to the entry index above.
- To regenerate the golden files after corpus changes: re-copy the affected
  file from `raw/`, apply the edit manually, and place in `golden/`.
