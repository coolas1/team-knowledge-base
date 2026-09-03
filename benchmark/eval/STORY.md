---
name: story
title: "Wren Adachi — Personal Archive (synthetic)"
description: Master narrative tying together a fabricated personal corpus spanning coral research, ambient music production, fermentation, and a 2024 Okinawa sabbatical. Now multi-language (English, Japanese, Mandarin) to test cross-language retrieval. Generated for PKM/RAG agent benchmarking — retrieval, modification, and knowledge-management evaluation.
type: original
created: 2023-06-01
updated: 2024-11-10
tags: [personal, persona, synthetic-corpus, hub, read-me-first, multilingual]
links:
  - research/coral-resilience-paper.md
  - research/lab-notebook.md
  - research/south-china-sea-coral-zh.md
  - music/polyp-tidal-ep-notes.md
  - music/field-recordings-okinawa.md
  - fermentation/recipes.md
  - travel/okinawa-2024/blog.md
  - notes/ideas.md
  - notes/multilingual-glossary.md
  - journal/2024-05-08.md
---

# Wren Adachi — Personal Archive

> **This corpus is synthetic.** It was generated in June 2026 to test multi-modal
> RAG agents against realistic personal archives. Every file here is fabricated;
> any resemblance to a real person is coincidental. Files span PDF, Excel, CSV,
> PNG, JPG, and Markdown so cross-modal retrieval can be evaluated end-to-end.

## Who is Wren?

Dr. **Wren Adachi** (34, she/her) is a marine biologist at UC Santa Cruz's
Institute of Marine Sciences. Three life-threads run through this archive:

| Thread                | What it produces                                            | Hub file                                  |
| --------------------- | ----------------------------------------------------------- | ----------------------------------------- |
| Coral reef research   | Academic paper, multi-sheet Excel, dive-site CSV, chart     | [research/coral-resilience-paper.md](research/coral-resilience-paper.md)     |
| Ambient music ("Polyp") | Album art, BPM/key CSV, field-recording transcripts        | [music/polyp-tidal-ep-notes.md](music/polyp-tidal-ep-notes.md)               |
| Fermentation hobby    | Recipes, batch-log Excel, pH time-series CSV               | [fermentation/recipes.md](fermentation/recipes.md)                          |

The connective tissue is a **six-week Okinawa sabbatical (March–April 2024)**
where all three threads collide: reef surveys for work, field recordings for
the next EP, and a deep dive into awamori and koji traditions. Read the trip
writeup: [travel/okinawa-2024/blog.md](travel/okinawa-2024/blog.md).

**Wren also works across three languages.** She is studying Japanese and
Mandarin, keeps some field notes and a journal in Japanese, and collaborates
with **Dr. Lin Wei (林伟)** at Xiamen University on South China Sea coral
bleaching. She explores **Chinese hongqu (红曲) red-yeast-rice fermentation**
alongside her Japanese koji work. The trilingual term table
[notes/multilingual-glossary.md](notes/multilingual-glossary.md) is the
multi-language hub; human-facing translations of this overview live at
[STORY_zh.md](STORY_zh.md) and [STORY_ja.md](STORY_ja.md).

## Why these files exist

A benchmarking corpus needs:

1. **Realistic cross-format references** — a journal entry mentions a paper
   draft; a blog mentions a photo; a recipe mentions a pH CSV. Multi-modal
   RAG agents should surface linked artifacts across formats.
2. **A non-trivial graph** — at least 30 nodes with 40+ edges. Start at
   [notes/ideas.md](notes/ideas.md) (the densest hub) or
   [research/lab-notebook.md](research/lab-notebook.md) and traverse.
3. **Format diversity** — Markdown (22), Excel (3), CSV (10), PDF (3),
   generated PNG (3), downloaded JPG (7). Each format stresses a different
   ingest pipeline.
4. **Multi-language coverage** — Japanese and Mandarin content is woven through
   every thread (research, music, fermentation, notes, journal). Some facts
   exist *only* in a non-English document, so cross-language retrieval is
   genuinely tested. The trilingual glossary
   ([notes/multilingual-glossary.md](notes/multilingual-glossary.md)) is the
   cross-language hub.

## Suggested test queries

These exercise the cross-link structure:

- "What did Wren observe about Cape Hedo and Sesoko West in 2023?"
  → should pull `coral-resilience-paper.pdf` Table 1 + `lab-notebook.md` + `figures/temp-vs-bleach.png`.
- "Which field recordings appear on the Tidal EP?"
  → should pull `field-recordings-okinawa.md` + `tracklist.csv` + `polyp-tidal-ep-notes.md`.
- "What did the Okinawa trip cost, and how does that compare to her monthly savings?"
  → should pull `travel/okinawa-2024/expenses.csv` + `notes/finance-2024.csv` + `journal/2024-05-08.md`.
- "How does Wren's koji process differ from her kombucha process?"
  → should pull `fermentation/recipes.md` + `batch-log.xlsx` + `ph-readings.csv`.
- "What bleaching threshold did Lin Wei's South China Sea survey report?"
  → should pull `research/scs-bleaching-survey-zh.pdf` (Chinese) +
  `south-china-sea-coral-zh.md` + `scs-bleaching-2024-zh.xlsx`.
- "How does Wren render the term for 'coral bleaching' across English, Japanese, and Chinese?"
  → should pull `notes/multilingual-glossary.md`.
- "What is the Japanese title on the JP-release cover of the Tidal EP, and what does it mean?"
  → should pull `music/tidal-ep-cover-ja.png` + `music/tidal-ep-liner-notes-ja.md`.

## Generated vs. downloaded

- **Generated locally**: PDFs (`reportlab`), Excels (`openpyxl`), CSVs,
  the SST-vs-bleaching chart (`matplotlib`), and the TIDAL album cover (`PIL`).
- **CJK binaries**: the Japanese/Chinese PDFs use reportlab's bundled Adobe CJK
  CID fonts (`HeiseiMin-W3` for Japanese, `STSong-Light` for Simplified
  Chinese) — no external font files. The Japanese-release cover (kanji 潮) is
  built as a reportlab PDF and rasterized with PyMuPDF, because this
  environment has no system CJK TTF usable by PIL.
- **Downloaded**: the seven JPGs use `picsum.photos` with deterministic seeds.
  Picsum returns generic stock images; the surrounding prose (especially
  `travel/okinawa-2024/blog.md`) supplies the semantic content. This mirrors
  real personal archives where photo filenames rarely describe their content.

## File inventory & relations

Every file in `raw/` is listed here. Markdown files originally carried YAML
frontmatter (title, description, type, created, updated, tags, and a `links:`
block); that metadata has been stripped from the files and consolidated here
so the corpus content is pure prose. Inline links inside each file's body are
**not** duplicated below — only the explicit `links:` block from the original
frontmatter is captured. Paths are relative to the file's own directory unless
they start with `../`.

### Markdown files (22)

#### research/

**coral-resilience-paper.md** — paper · 2024-09-12 → 2024-11-08
*Thermal Forcing and Coral Bleaching Resilience in the Ryukyu Archipelago*
Markdown mirror of Wren's first-author paper. PDF is the canonical version;
this file exposes the abstract, key findings, and figure references for
full-text search.
Tags: research, coral, ryukyu, climate, peer-reviewed, first-author
Links to: `coral-resilience-paper.pdf`, `bleaching-survey-2024.xlsx`,
`figures/temp-vs-bleach.png`, `dive-sites.csv`, `lab-notebook.md`,
`../journal/2024-08-22.md`

**lab-notebook.md** — note · 2020-01-15 → 2024-11-08
*Lab notebook — Ryukyu coral resilience project*
Running day-to-day research notes from the four-year coral survey, with
emphasis on the 2023 heatwave, symbiont metabarcoding, and the 2024 paper
revision cycle.
Tags: research, lab-notebook, coral, ryukyu, long-running
Links to: `coral-resilience-paper.md`, `coral-resilience-paper.pdf`,
`bleaching-survey-2024.xlsx`, `figures/temp-vs-bleach.png`, `dive-sites.csv`,
`../journal/2023-11-15.md`, `../journal/2024-08-22.md`,
`../travel/okinawa-2024/blog.md`

**sesoko-field-notes-ja.md** — note · 2024-03-08 → 2024-04-02 · *Japanese*
*瀬底西 サンゴ礁フィールドノート（日本語版）*
Markdown mirror of Wren's Japanese-language Sesoko West field notes (PDF is
canonical). Records the *Cladocopium* C3k observation (Shannon H′ = 0.82) and
the March 15 bottom temperature — facts that appear only here, in Japanese.
Tags: research, coral, sesoko, field-notes, japanese, multilingual
Links to: `sesoko-field-notes-ja.pdf`, `dive-sites-ja.csv`,
`coral-resilience-paper.md`, `lab-notebook.md`,
`../music/field-recordings-okinawa.md`, `../notes/ideas.md`,
`../notes/multilingual-glossary.md`

**south-china-sea-coral-zh.md** — note · 2024-06-01 → 2024-09-30 · *Chinese*
*南海珊瑚白化调查（中文版）*
Markdown mirror of Lin Wei's Chinese-language South China Sea bleaching report
(collaboration with Wren). Carries the DHW = 7.8 breakpoint and the 22%→58%
summer trend, which appear only in Chinese.
Tags: research, coral, south-china-sea, chinese, multilingual, collaboration
Links to: `scs-bleaching-survey-zh.pdf`, `scs-bleaching-2024-zh.xlsx`,
`scs-dive-sites-zh.csv`, `photos/scs-reef-zh.jpg`, `coral-resilience-paper.md`,
`lab-notebook.md`, `../notes/ideas.md`, `../notes/multilingual-glossary.md`

#### music/

**polyp-tidal-ep-notes.md** — original · 2023-11-22 → 2024-06-30
*Polyp — Tidal EP: production diary*
Production notes for the six-track ambient EP "Tidal" released under Wren's
alias Polyp. Built around field recordings from the Okinawa sabbatical.
Tags: music, polyp, ambient, ep, production, field-recordings
Links to: `album-cover.png`, `tracklist.csv`, `field-recordings-okinawa.md`,
`setlist-2024-04-12.md`, `../journal/2023-11-15.md`, `../journal/2024-05-08.md`,
`../notes/ideas.md`

**field-recordings-okinawa.md** — note · 2024-04-20 → 2024-06-15
*Field recordings — Okinawa 2024*
Transcripts and descriptions of the four primary field recordings that anchor
the Tidal EP. Source WAVs are not in this corpus; this file preserves what
each recording captured and which track uses it.
Tags: music, field-recording, okinawa, transcript, polyp
Links to: `tracklist.csv`, `polyp-tidal-ep-notes.md`,
`../travel/okinawa-2024/blog.md`, `../travel/okinawa-2024/photos/dive-1.jpg`,
`../travel/okinawa-2024/photos/reef-pano.jpg`, `../journal/2024-05-08.md`

**setlist-2024-04-12.md** — note · 2024-04-13 → 2024-04-15
*Polyp live — Small Quiet Rooms, 2024-04-12*
Live set setlist and notes for Polyp's first public performance of the Tidal
material, at Small Quiet Rooms in Berkeley.
Tags: music, polyp, live, setlist, performance
Links to: `polyp-tidal-ep-notes.md`, `tracklist.csv`,
`field-recordings-okinawa.md`, `../journal/2024-05-08.md`

**tidal-ep-liner-notes-ja.md** — note · 2024-06-15 · *Japanese*
*『Tidal』EP 日本盤ライナーノーツ*
Japanese-release liner notes for the Tidal EP. Documents the kanji cover title
潮 (shio, "tide") and the Japanese tracklist. The cover PNG is the multi-modal
artifact for cross-language image questions.
Tags: music, polyp, tidal-ep, liner-notes, japanese, multilingual
Links to: `tidal-ep-cover-ja.png`, `tracklist-ja.csv`, `polyp-tidal-ep-notes.md`,
`field-recordings-okinawa.md`, `../notes/multilingual-glossary.md`

#### fermentation/

**recipes.md** — note · 2023-05-04 → 2024-10-22
*Wren's fermentation recipes (working collection)*
Recipes for kombucha, koji, hot sauce, kimchi, and miso — the five ferments
Wren keeps in active rotation. Batch-by-batch data lives in batch-log.xlsx;
pH trajectories in ph-readings.csv.
Tags: fermentation, recipes, kombucha, koji, hot-sauce, kimchi, miso, hobby
Links to: `batch-log.xlsx`, `ph-readings.csv`, `photos/scoby-jar.jpg`,
`../journal/2024-03-01.md`, `../notes/ideas.md`,
`../travel/okinawa-2024/blog.md`

**hongqu-notes-zh.md** — note · 2024-07-01 → 2024-07-19 · *Chinese*
*红曲（hongqu）发酵笔记（中文版）*
Chinese-language notes on red-yeast-rice (红曲, *Monascus purpureus*)
fermentation, contrasted with Japanese koji. Carries the 32–35°C optimum and
the pH ~4.5 pigment peak — facts that appear only in Chinese.
Tags: fermentation, hongqu, red-yeast-rice, chinese, multilingual
Links to: `hongqu-ph-readings-zh.csv`, `photos/hongqu-rice-zh.jpg`, `recipes.md`,
`../notes/ideas.md`, `../notes/multilingual-glossary.md`

**awamori-notes-ja.md** — note · 2024-03-10 → 2024-03-24 · *Japanese*
*泡盛（awamori）ノート（日本語版）*
Japanese-language notes on awamori (泡盛, black-koji spirit) from the Nago
brewery. The best batch A-003 reached final gravity 0.984 and ABV 43.2%.
Tags: fermentation, awamori, black-koji, japanese, multilingual
Links to: `awamori-batch-ja.csv`, `recipes.md`,
`../travel/okinawa-2024/blog.md`, `../journal/2024-03-20-ja.md`,
`../notes/multilingual-glossary.md`

#### travel/okinawa-2024/

**blog.md** — original · 2024-05-04 → 2024-05-08
*Six weeks between reefs and jars*
Travel blog for the March–April 2024 Okinawa sabbatical. Wove together coral
surveys, field recordings, and awamori/koji exploration. Photos and expenses
are linked from here.
Tags: travel, okinawa, blog, sabbatical, reef-diving, fermentation,
field-recording
Links to: `itinerary.md`, `expenses.csv`, `photos/dive-1.jpg`,
`photos/dive-2.jpg`, `photos/reef-pano.jpg`, `photos/market.jpg`,
`../../research/dive-sites.csv`, `../../research/lab-notebook.md`,
`../../music/field-recordings-okinawa.md`, `../../music/polyp-tidal-ep-notes.md`,
`../../fermentation/recipes.md`, `../../journal/2024-03-01.md`,
`../../journal/2024-05-08.md`

**itinerary.md** — note · 2024-02-10 → 2024-04-22
*Okinawa 2024 — day-by-day itinerary*
Planned and actual itinerary for the six-week Okinawa sabbatical.
Cross-referenced from blog.md; expenses in expenses.csv.
Tags: travel, okinawa, itinerary, planning
Links to: `blog.md`, `expenses.csv`, `../../research/dive-sites.csv`,
`../../journal/2024-03-01.md`

#### journal/

**2023-11-15.md** — journal · 2023-11-15
*Journal — 2023-11-15*
Pre-sabbatical entry. NSF grant panel came back hard; spent the weekend in
the studio sketching what became the Polyp EP's title track.
Tags: journal, nsf, stress, music, sketches
Links to: `../research/lab-notebook.md`, `../music/polyp-tidal-ep-notes.md`,
`../notes/ideas.md`

**2024-03-01.md** — journal · 2024-03-01
*Journal — 2024-03-01*
First day of the Okinawa sabbatical. Travel, jet lag, and a guesthouse
kitchen that turned into an impromptu kimchi session.
Tags: journal, okinawa, travel, arrival
Links to: `../travel/okinawa-2024/blog.md`,
`../travel/okinawa-2024/itinerary.md`, `../travel/okinawa-2024/expenses.csv`,
`../research/dive-sites.csv`, `../fermentation/recipes.md`

**2024-05-08.md** — journal · 2024-05-08
*Journal — 2024-05-08*
Three weeks back from Okinawa. The EP is mostly mixed; the paper revisions
are starting; the miso crock is bubbling. A reflective entry.
Tags: journal, reflection, post-trip, music, paper, fermentation
Links to: `../travel/okinawa-2024/blog.md`,
`../music/polyp-tidal-ep-notes.md`, `../music/field-recordings-okinawa.md`,
`../music/setlist-2024-04-12.md`, `../research/lab-notebook.md`,
`../fermentation/recipes.md`, `../notes/ideas.md`

**2024-08-22.md** — journal · 2024-08-22
*Journal — 2024-08-22*
Mid-revision hell on the coral paper. Submission target slipped twice.
The miso tastes right, which is something.
Tags: journal, paper-revisions, stress, miso
Links to: `../research/coral-resilience-paper.md`,
`../research/coral-resilience-paper.pdf`, `../research/lab-notebook.md`,
`../research/figures/temp-vs-bleach.png`, `../fermentation/recipes.md`,
`../fermentation/batch-log.xlsx`

**2024-03-20-ja.md** — journal · 2024-03-20 · *Japanese*
*Journal — 2024-03-20（日本語）*
A journal entry written in Japanese as practice: the Nago awamori brewery
revisit, a Sesoko logger pull, and the start of the trilingual glossary.
Tags: journal, okinawa, japanese, multilingual, language-practice
Links to: `../fermentation/awamori-notes-ja.md`,
`../research/sesoko-field-notes-ja.md`, `../notes/multilingual-glossary.md`,
`../notes/mandarin-study-zh.md`

#### notes/

**ideas.md** — note · 2022-04-04 → 2024-11-10
*Ideas — cross-domain sparks*
The densest hub in the corpus. Wren's running list of ideas that cross the
boundaries between coral research, ambient music, and fermentation. Linked
from everywhere.
Tags: ideas, hub, cross-domain, resilience, sound, fermentation
Links to: `../STORY.md` (this file, relative to `raw/notes/`),
`../research/coral-resilience-paper.md`, `../research/lab-notebook.md`,
`../music/polyp-tidal-ep-notes.md`, `../music/field-recordings-okinawa.md`,
`../fermentation/recipes.md`, `reading-list.md`, `../journal/2024-05-08.md`,
`../journal/2023-11-15.md`, `../travel/okinawa-2024/blog.md`

**reading-list.md** — note · 2023-01-10 → 2024-10-05
*Reading list — books in active rotation*
Books Wren has read, is reading, or plans to read. Grouped by the three
life-threads plus a general section. Starred books are ones she'd recommend.
Tags: reading, books, reference
Links to: `../research/lab-notebook.md`,
`../music/polyp-tidal-ep-notes.md`, `../fermentation/recipes.md`, `ideas.md`

**multilingual-glossary.md** — note · 2024-03-20 → 2024-11-01 · *EN/JA/ZH*
*多语言术语表 / 多言語用語集 / Multilingual glossary*
The cross-language hub: a trilingual (EN/JA/ZH) term table for coral, symbiont,
fermentation, and ocean terms. Dense outbound links across all threads.
Tags: glossary, multilingual, hub, japanese, chinese
Links to: `../research/coral-resilience-paper.md`,
`../research/sesoko-field-notes-ja.md`, `../research/south-china-sea-coral-zh.md`,
`../fermentation/recipes.md`, `../fermentation/hongqu-notes-zh.md`,
`../fermentation/awamori-notes-ja.md`, `mandarin-study-zh.md`,
`../journal/2024-03-20-ja.md`

**mandarin-study-zh.md** — note · 2024-03-05 → 2024-10-20 · *Chinese*
*普通话学习笔记（中文版）*
Wren's Mandarin study log: scientific and fermentation vocabulary, a weekly
exchange-partner cadence, and reading pointers.
Tags: language, mandarin, chinese, study-log, multilingual
Links to: `multilingual-glossary.md`, `reading-list.md`,
`../research/south-china-sea-coral-zh.md`, `../journal/2024-03-20-ja.md`

### Attachment files (no frontmatter — leaf nodes)

#### research/
- `coral-resilience-paper.pdf` — canonical 8-page paper (reportlab-generated)
- `bleaching-survey-2024.xlsx` — 4 sheets: site summaries, species cover,
  monthly SST, quarterly bleaching %
- `dive-sites.csv` — 12 Ryukyu reef sites (RYK-01..RYK-12) with coordinates
  and dive metadata
- `figures/temp-vs-bleach.png` — SST × bleaching scatter, colored by site
  cluster (matplotlib)
- `sesoko-field-notes-ja.pdf` — Japanese-language Sesoko West field notes,
  2024 spring survey (reportlab + `HeiseiMin-W3` CJK font)
- `scs-bleaching-survey-zh.pdf` — Chinese-language South China Sea bleaching
  report by Lin Wei (reportlab + `STSong-Light` CJK font)
- `scs-bleaching-2024-zh.xlsx` — 3 Chinese-named sheets (站点 / 月度水温 /
  季度白化百分比) (openpyxl)
- `dive-sites-ja.csv` — 12 Ryukyu sites, Japanese headers and site names
- `scs-dive-sites-zh.csv` — 6 South China Sea sites, Chinese headers
- `photos/scs-reef-zh.jpg` — South China Sea reef scene (picsum)

#### music/
- `album-cover.png` — Tidal EP cover art (PIL-generated)
- `tracklist.csv` — 6 tracks with BPM, key, length, source recording
- `tidal-ep-cover-ja.png` — Japanese-release cover; title 潮 (reportlab PDF
  rasterized via PyMuPDF — no system CJK font for PIL in this env)
- `tracklist-ja.csv` — 6 tracks with Japanese titles

#### fermentation/
- `batch-log.xlsx` — every fermentation batch (B-001 onward): start date,
  temp, gravity, notes
- `ph-readings.csv` — pH time-series for three representative batches
  (B-007, B-013, B-018)
- `photos/scoby-jar.jpg` — Wren's active SCOBY hotel (picsum)
- `hongqu-ph-readings-zh.csv` — 红曲 fermentation pH/temperature time-series,
  Chinese headers (batch HQ-002)
- `awamori-batch-ja.csv` — 泡盛 mash batches, Japanese headers (A-003 = 0.984
  final gravity, 43.2% ABV)
- `photos/hongqu-rice-zh.jpg` — red yeast rice (picsum)

#### travel/okinawa-2024/
- `expenses.csv` — every yen spent, categorized
- `photos/dive-1.jpg`, `photos/dive-2.jpg` — Yonaguni West drift dive
  (RYK-01)
- `photos/reef-pano.jpg` — Sesoko reef panorama
- `photos/market.jpg` — Makishi market / awamori bottling stall

#### notes/
- `finance-2024.csv` — monthly finance snapshot for comparison with trip
  cost

### Graph summary

- **Highest in-degree (markdown hubs)**: `notes/ideas.md`,
  `travel/okinawa-2024/blog.md`, and `research/lab-notebook.md` remain the most
  linked files. `notes/multilingual-glossary.md` joins them as a new
  cross-language hub with dense outbound links across every thread.
- **Strongest cross-thread connectors**: `travel/okinawa-2024/blog.md`
  references files spanning all five other top-level directories; the new
  `notes/multilingual-glossary.md` similarly bridges research, fermentation,
  music, notes, and journal.
- **Multi-language cluster**: 8 Japanese/Chinese markdown files plus 11 CJK
  binary leaf nodes connect into the existing graph via the glossary and the
  trilingual-perspective idea in `notes/ideas.md`.
- **Leaf nodes**: every PDF / XLSX / CSV / PNG / JPG file (including the CJK
  ones) has only incoming links — no outgoing edges.

## Regenerating this corpus

```bash
# All binaries:
uv run --with reportlab --with openpyxl --with matplotlib --with Pillow \
    --with PyMuPDF scripts/generation/scaffold-personal.py
# Only the Japanese/Chinese (CJK) binaries:
uv run --with reportlab --with openpyxl --with Pillow --with PyMuPDF \
    scripts/generation/scaffold-personal.py --only cjk
```

The script is idempotent. Random seeds are fixed (Python `random.Random(42)`,
numpy `default_rng(7)`) so output is byte-reproducible modulo Picsum's
return values. Markdown files are hand-authored under `raw/` (not generated).
Approach and design decisions are documented in
[scripts/generation/README.md](../../scripts/generation/README.md).
