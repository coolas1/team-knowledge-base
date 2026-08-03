# Multi-modal retrieval benchmark — 40 QAs

## Purpose

Evaluate PKM, RAG, and AI agents (currently gbrain, WeKnora, and
team-knowledge-base) against the synthetic "Wren Adachi" personal archive
under `raw/`. The
corpus spans six format families (Markdown, PDF, Excel, CSV, generated PNG,
stock JPG) and is intentionally cross-linked so graph traversal,
multi-modal parsing, and temporal reasoning are all load-bearing.

Each QA pair tests one of four retrieval failure modes:

| Code | Meaning                 | Count | What it stress-tests                              |
| ---- | ----------------------- | ----- | ------------------------------------------------- |
| D    | Detail locating         | 11    | Single-fact extraction from one specific file     |
| L    | Link following          | 10    | Multi-hop traversal across 2+ cross-referenced files |
| T    | Temporal / date range   | 8     | Time-series, date arithmetic, range queries       |
| M    | Multi-modal             | 11    | Requires PDF / xlsx / image parsing, not just text |

Total: **40 QAs**.

> **Cross-lingual (X) overlay.** Questions **31–40** additionally exercise
> Japanese (JA) / Chinese (ZH) retrieval: the query language and/or the source
> language is non-English. Each such file carries `language: cross-lingual`,
> `query_language` (en/ja/zh), and `source_language` (ja/zh/multi) frontmatter.
> Some facts exist *only* in a non-English document, and two of the queries are
> written in Japanese/Chinese themselves.

## Corpus

- Master narrative: [`eval/STORY.md`](../STORY.md)
- Generator (source of truth for binary files): [`scripts/generation/scaffold-personal.py`](../../scripts/generation/scaffold-personal.py)
- Corpus root: [`raw/`](../../raw/) — ~48 files across `research/`, `music/`, `fermentation/`, `journal/`, `notes/`, `travel/`; now multi-language (English, Japanese, Chinese)

## File layout

```
eval/qa/
├── README.md                  ← this file (format, index, grading rules)
├── PREDICT_INSTRUCTIONS.md    ← runbook for prediction agents
├── EVAL_INSTRUCTIONS.md       ← runbook for scoring agents
├── questions/NN-q.md          ← question file (frontmatter + question text)
└── answers/NN-a.md            ← ground-truth answer (answer + sources + reasoning
                                + gotchas + variants)
```

IDs are zero-padded 01–40. Numbering encodes category:
- `01–07` = D (detail)
- `08–14` = L (link)
- `15–21` = T (temporal)
- `22–30` = M (multi-modal)
- `31–40` = cross-lingual (X) overlay — these still map to D/L/T/M via `category`,
  but additionally carry a JA/ZH query- or source-language dimension

Per-app output is written outside this directory, under
`<appname>-files/qa/`: `predictions/NN-p.md` (one prediction per question) and
`RESULTS.md` (the scored report). The two runbooks above define those workflows.

## Question index

| ID  | Cat | Summary                                                                           | Inconsistency? |
| --- | --- | --------------------------------------------------------------------------------- | -------------- |
| 01  | D   | SST logger model + sampling interval from the paper                               |                |
| 02  | D   | Longest track on the *Tidal* EP + length                                          |                |
| 03  | D   | Book described as "made me start recording ambient music seriously"               |                |
| 04  | D   | Dive site with the deepest typical depth                                         |                |
| 05  | D   | Timestamp the NSF panel reviews landed in Wren's inbox                            |                |
| 06  | D   | Koji fungus species + preferred rice variety                                      |                |
| 07  | D   | Three text lines on the Tidal album cover                                         |                |
| 08  | L   | Track dedicated to a paper refugium + site_id                                     |                |
| 09  | L   | 2023-11-15 sketch → track, with BPM/key shift                                     | ✓ (sketch vs release) |
| 10  | L   | Paper reference ↔ "reframed how I think about symbionts"                          |                |
| 11  | L   | `ideas.md` "big one" + 3 source files it pulls from                               |                |
| 12  | L   | SCUBA inhale sample → dive site + track                                           |                |
| 13  | L   | Triad: site in paper refugia ∩ EP sample ∩ journal inhale                          |                |
| 14  | L   | Months where travel cost drove savings negative                                   |                |
| 15  | T   | RYK-03 max quarterly bleaching + which quarter                                    |                |
| 16  | T   | Days between NSF panel receipt and resubmission lab entry                         |                |
| 17  | T   | Date DHW > 10 confirmations came back for the two refugia                         |                |
| 18  | T   | Paper submission target date                                                      |                |
| 19  | T   | Okinawa sabbatical start and end dates                                            |                |
| 20  | T   | Date of B-019's 100-day mark (inferred from "Tuesday" in 2024-08-22 entry)        | ✓ (B-019 type/start date conflicts across sources) |
| 21  | T   | Lab-notebook entry documenting the R² change, with old/new values + reason        |                |
| 22  | M   | Album cover visual design (colors, central graphic, all text)                     |                |
| 23  | M   | Three site clusters + R² on the temp-vs-bleach scatter plot                       |                |
| 24  | M   | Max SST across all sites and months in `SST_2024_monthly`                         |                |
| 25  | M   | Bleaching-DHW breakpoint + 95% CI from the PDF                                    |                |
| 26  | M   | dive-1.jpg dive site + recording it documents (image + prose cross-ref)           |                |
| 27  | M   | Ferment type with highest mean `rating_1to5` in `batch-log.xlsx`                  |                |
| 28  | M   | Type of batch B-013 across `recipes.md` / `batch-log.xlsx` / `ph-readings.csv`    | ✓ (3-way conflict) |
| 29  | M   | Trip total JPY: `blog.md` quote vs `expenses.csv` sum                             | ✓ (blog states full total; CSV is partial) |
| 30  | M   | Chart cluster that sits at low-bleach despite high SST + paper §3.3 cross-ref     |                |
| 31  | D   | Rare symbiont (C3k, H′=0.82) from the Japanese Sesoko field notes (ja source)     |                |
| 32  | D   | South China Sea DHW breakpoint + 95% CI from the Chinese report (zh source)       |                |
| 33  | L   | Hongqu vs koji: microbe + optimum temperature (zh ↔ en)                          |                |
| 34  | M   | Best awamori batch: final gravity / ABV / start date (ja CSV)                     |                |
| 35  | T   | SCS survey date range + bleaching trend (zh report + xlsx)                        |                |
| 36  | D   | "Coral bleaching" across EN/JA/ZH in the trilingual glossary                      |                |
| 37  | L   | Tidal EP recording gear — Japanese query, English source (ja→en)                  |                |
| 38  | D   | Hongqu pigment optimum pH + peak day — Chinese query (zh→zh)                      |                |
| 39  | L   | 2024-03-15 Sesoko West temp: EN lab notebook vs JA field notes                    | ✓ (22.7 °C EN vs 23.4 °C JA) |
| 40  | M   | Kanji title (潮) on the JP-release Tidal cover + meaning (ja image)               |                |

## Grading notes

- **Single precise answer.** Each Q has exactly one right answer; expression may vary (see "Acceptable answer variants" in each A file).
- **Partial credit.** A response that names the right entity but misses the supporting citation chain counts as partial.
- **Inconsistency handling.** For Qs flagged above (09, 20, 28, 29), full credit requires *discovering and naming the discrepancy* across sources — a retrieval agent that silently picks one side loses points. The expected behavior is to surface both values and (where possible) explain which source is more authoritative.
- **Cross-modal verification.** M-class questions require actually parsing the binary file (PDF/xlsx/PNG). A text-only RAG that derives the answer from a markdown mirror alone is partially correct but should be marked down for not engaging the modality.
- **Cross-lingual handling.** For Qs 31–40, answers may be given in English or in the query's/source's language; either is acceptable as long as the facts and sources are correct. Credit agents that cite the correct cross-lingual source file even when the query was in a different language, and (for Q39) that surface both sides of a cross-language discrepancy.

## Known corpus inconsistencies

A retrieval agent surveyed on this benchmark should be able to discover and
report these. They are intentionally embedded.

1. **Batch B-013 type** — `fermentation/recipes.md` (Koji section) calls B-013 a koji batch; `fermentation/batch-log.xlsx` calls it kimchi at 21.4 °C; `fermentation/ph-readings.csv` shows B-013 held at ~30 °C, which supports the koji classification. (Q28)
2. **Batch B-019 type & start date** — `batch-log.xlsx` says hot_sauce, started 2024-02-13; `recipes.md` and `journal/2024-05-08.md` say miso started ~2024-05-10; `journal/2024-08-22.md` says B-019 hit its 100-day mark on Tuesday 2024-08-20, which math-wise supports the early-May start. (Q20)
3. **Trip total JPY** — `travel/okinawa-2024/blog.md` states "Total JPY 387,200 ≈ USD 2,616"; `expenses.csv` sums to JPY 120,200 (USD ~812) across its 19 receipts. The CSV is a partial record; the blog states the full trip total. (Q29)
4. **Polyp sketch vs release** — `journal/2023-11-15.md` describes the original sketch as 70 BPM, F# minor; `music/tracklist.csv` and `polyp-tidal-ep-notes.md` release T2 "Polyp" at 72 BPM, A minor. The tempo/key shifted between sketch and release. (Q09)
5. **Island groups count** — Paper §2.1 says "Twelve sites were selected across four island groups (Yonaguni, Ishigaki, Okinawa-honto, Amami-oshima)" but `bleaching-survey-2024.xlsx` Sites sheet actually spans five groups (the four listed plus Kerama). Quietly listing five groups while saying "four" is a realistic prose-vs-data drift.
6. **expenses.csv date coverage** — `itinerary.md` documents the trip ran March 1 – April 19, but every row in `expenses.csv` is dated March (the generator only emits March receipts). April expenses appear only in `finance-2024.csv` as the aggregated `travel_usd=2800` line.
7. **2024-03-15 Sesoko West temperature** — the English `research/lab-notebook.md` records the bottom temp (15 m) as **22.7 °C**, while the Japanese `research/sesoko-field-notes-ja.pdf` / `.md` records **23.4 °C** for the same dive. The JA journal `journal/2024-03-20-ja.md` repeats the 23.4 °C value. (Q39)

## Regenerating / extending

- All Qs and As are hand-written from the actual file contents (not from the generator script's source constants). The script is consulted only where the markdown mirror doesn't carry the data (e.g., xlsx bleaching percentages, PNG chart R², album-cover typography).
- To add a new QA: pick the next free ID, follow the templates at the top of any existing `questions/NN-q.md` / `answers/NN-a.md`, and add a row to the question index above.
- To regenerate the underlying corpus: `uv run --with reportlab,openpyxl,Pillow,requests,matplotlib,PyMuPDF scripts/generation/scaffold-personal.py` (idempotent; seed-fixed). Add `--only cjk` to regenerate just the Japanese/Chinese binaries.
