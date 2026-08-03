# Personal corpus generation

This directory holds the generator for `raw/` — a synthetic multi-modal corpus
representing one fictional person's (Dr. Wren Adachi's) archive. The corpus
exists so PKM, RAG, and AI agents (currently gbrain, WeKnora, and
team-knowledge-base) can be benchmarked against realistic personal data that
spans formats, languages
(English, Japanese, Mandarin), and cross-references itself.

The generator is **one Python script** plus the markdown files, which are
written by hand (via the Write tool, not the script) so the cross-links stay
reviewable.

```
scripts/generation/
├── README.md                  ← this file
└── scaffold-personal.py       ← generates the 15 binary files
```

The markdown files live under `raw/` alongside the generated
binaries. The split (script writes binaries, human writes prose) is
deliberate: prose carries the semantic cross-links that make the corpus
useful for retrieval testing, and we want those readable in diffs.

---

## Design approach

### 1. Persona as a randomly-combined bundle of threads

The corpus is built around one persona — **Dr. Wren Adachi**, a marine
biologist at UC Santa Cruz — but the persona itself is a random
combination of three unrelated life-threads:

| Thread               | Career hook                | Output formats it produces                   |
| -------------------- | -------------------------- | -------------------------------------------- |
| Coral reef research  | Day job (postdoc)          | PDF paper, Excel survey, CSV dive data, chart PNG |
| Ambient music ("Polyp") | Side project            | PNG cover art, CSV tracklist, prose transcripts of field recordings |
| Fermentation hobby   | Weekend / evenings         | Excel batch log, CSV pH time-series, recipe markdown, JPG jar photo |

The three threads meet during a **six-week Okinawa sabbatical in 2024**.
This is the structural move that earns the cross-document links: the same
trip produces dive data for the paper, hydrophone recordings for the EP,
and awamori distillery visits that inform the fermentation hobby. Links
between files then feel narrative rather than imposed.

**Why this design pattern**:
- Maximizes format diversity without a contrived "Wren wrote a PDF, and
  also an Excel, and also a CSV" feel.
- Produces realistic cross-links — a journal entry naturally references
  the paper draft, the EP sketch, and the batch log.
- A RAG agent tested against this corpus has to handle *format variety*
  (text ↔ table ↔ image ↔ PDF), *graph traversal* (~48 files; 22 markdown
  nodes, denser after the multi-language extension), and *cross-language
  retrieval* (JA/ZH content, with some facts existing only in a non-English doc).

### 2. Mixed media: generated vs downloaded

Different files use different generation strategies, picked per content
type:

| Strategy         | Used for                                              | Why                                                    |
| ---------------- | ----------------------------------------------------- | ------------------------------------------------------ |
| **Generated (matplotlib)** | `research/figures/temp-vs-bleach.png`      | Real numeric relationships — a vision agent can answer "what's the SST–bleaching correlation?" from the chart alone. |
| **Generated (PIL)**        | `music/album-cover.png`                     | Full design control (gradient, typography, fake waveform). Album art is design, not data. |
| **Generated (reportlab)**  | `research/coral-resilience-paper.pdf`       | Real text content with abstract, sections, table, references. Parseable by any PDF pipeline. |
| **Generated (openpyxl)**   | 2 Excel workbooks                          | Real sheets with formulas, styled headers, conditional fills (refugia columns highlighted). |
| **Generated (stdlib `csv`)** | 5 CSVs                                    | Trivial; no library needed.                            |
| **Downloaded (Picsum)**    | 5 JPGs (dive photos, reef pano, market, scoby) | Photorealistic texture; semantic content carried by surrounding prose because Picsum's seed→image mapping is arbitrary. |

The Picsum fallback in the script (captioned PIL placeholder on download
failure) means the script is **network-optional**: if Picsum is
unreachable, the corpus is still structurally complete; only the JPGs
become obviously synthetic.

### 3. Reproducibility

- Python `random.Random(42)` for the script's own RNG (CSV row counts,
  Excel bleaching percentages, etc.).
- numpy `np.random.default_rng(7)` for the matplotlib chart.
- Picsum URLs are deterministic (`/seed/<string>/<w>/<h>`), so the same
  photo downloads every run.
- The script is **idempotent** — overwrites cleanly on re-run, safe to
  invoke repeatedly.
- Result: byte-reproducible output modulo Picsum's upstream catalog.

### 4. Cross-link graph

Markdown files under `raw/` are **pure prose with no frontmatter** — metadata
(title, type, dates, tags) and the consolidated link graph live in
`eval/STORY.md` (see the project `CLAUDE.md`). Cross-references are expressed
as **inline markdown links** in each file's body; targets are relative to the
linking file's own directory (same-dir, `../sibling`, or `../../`). Hub files
that anchor the graph: `eval/STORY.md`, `notes/ideas.md`,
`travel/okinawa-2024/blog.md`, and the trilingual `notes/multilingual-glossary.md`.

Three hub files exist intentionally:
- `STORY.md` — top-level orientation
- `notes/ideas.md` — densest hub (10 outbound links), the cross-domain
  "sparks" file
- `travel/okinawa-2024/blog.md` — connective tissue across threads

The link integrity is verified end-to-end (see Verification below).

---

## Script architecture

`scaffold-personal.py` is structured as five writer functions plus a
small CLI driver:

```python
write_pdf(target)              # reportlab → 1 PDF (paper)
write_excels(base)             # openpyxl  → 2 .xlsx (survey + batch log)
write_csvs(base)               # stdlib    → 5 .csv
write_chart(target)            # matplotlib → 1 PNG (SST × bleaching scatter)
write_album_cover(target)      # PIL       → 1 PNG (TIDAL cover)
download_one(target, seed, w, h)  # requests/urllib → 1 JPG, PIL fallback
```

Driver supports:
- `--dry-run` — print the 15 planned files without writing
- `--only {pdf,excel,csv,images,download}` — regenerate one section

The markdown files are **not** generated by this script. They live under
`raw/` and are written by hand. To regenerate them, edit the
files in place; there's nothing to re-run.

### Content philosophy

Where the script embeds text content (paper abstract, recipe ingredients,
fermentation batch notes), the content is **specific rather than generic**.
Real numbers, real species names, real brands (`Cold Mountain` koji-kin,
`Zoom H5`, `Small Quiet Rooms`). Specificity is what lets a RAG agent
find the right chunk on a query like "what did Wren observe about Cape
Hedo in 2023" — lorem ipsum would defeat the test.

---

## Running

```bash
# From repo root. uv handles all deps in an ephemeral venv.
uv run --with reportlab --with openpyxl --with matplotlib --with Pillow \
    --with PyMuPDF scripts/generation/scaffold-personal.py
# Regenerate only the Japanese/Chinese (CJK) binaries:
uv run --with reportlab --with openpyxl --with Pillow --with PyMuPDF \
    scripts/generation/scaffold-personal.py --only cjk
```

That command regenerates all binary files (15 English + 11 CJK) directly into
`raw/`, preserving the hand-written markdown files. Markdown is **not**
generated by this script.

To preview without writing:

```bash
uv run --with reportlab --with openpyxl --with matplotlib \
    scripts/generation/scaffold-personal.py --dry-run
```

To regenerate just the PDF (e.g. after editing the `PAPER` dict):

```bash
uv run --with reportlab \
    scripts/generation/scaffold-personal.py --only pdf
```

---

## Verification

End-to-end check that the corpus is well-formed:

```bash
uv run --with pypdf --with Pillow --with openpyxl python -c "
from pathlib import Path
from pypdf import PdfReader
from PIL import Image
from openpyxl import load_workbook

root = Path('raw')
print(f'markdown files: {len(list(root.rglob(\"*.md\")))} (pure prose, no frontmatter)')
for pdf in root.rglob('*.pdf'):
    n = len(PdfReader(str(pdf)).pages)
    assert n >= 1, f'bad pdf: {pdf}'
    print(f'  pdf ok: {pdf.relative_to(root)} ({n} pages)')
for p in list(root.rglob('*.png')) + list(root.rglob('*.jpg')):
    Image.open(p).verify()
for x in root.rglob('*.xlsx'):
    load_workbook(x)
print('images + xlsx: all valid')
print('(cross-link integrity is checked by the ingest pipeline extract step,')
print(' not here — raw/ markdown carries no frontmatter links block.)')
"
```

Expected on a clean run: `markdown files: 22`, every PDF reported ok, and
`images + xlsx: all valid`.

---

## Known limitations and gotchas

- **PDF is 5 pages, not the planned 8.** reportlab packs text more tightly
  than the page-count estimate. Content is complete (abstract + 5
  sections + 12-row table + 7 references). Bump `body.fontSize` from
  10.5 → 12 in `write_pdf()` if you want closer to 8 pages.
- **Picsum images are generic stock photos**, not actually Okinawa reefs
  or scoby jars. The seed strings (`okinawa-dive-1`, `scoby-jar`, etc.)
  do **not** map to semantically-matched images — they're just stable
  identifiers for reproducibility. The semantic content lives in the
  surrounding prose (especially `travel/okinawa-2024/blog.md`).
- **Unsplash was unreachable** from this network when the corpus was
  generated (`curl` timed out). If Unsplash source URLs come back online,
  they'd give more scene-appropriate photos for the travel thread — swap
  them in by editing `PICSUM_TARGETS`.
- **Audio/video files are intentionally absent.** No realistic way to
  synthesize speech or video; the format diversity (PDF/Excel/CSV/PNG/JPG/MD)
  is already enough to stress a multi-modal ingest pipeline. The music
  thread uses markdown transcripts (`field-recordings-okinawa.md`) to
  represent audio content in a text-searchable form.
- **Excel/CSV aren't natively ingestable by gbrain today** (per Explore
  findings on `gbrain/src/core/sync.ts`). They're still in the corpus
  because other apps under test may handle them, and because they
  participate in the cross-link graph from markdown hubs.

---

## Extending the corpus

To add more files:

1. **Another binary**: extend the relevant writer function in
   `scaffold-personal.py` (e.g. add a row to `PICSUM_TARGETS` for another
   photo, or extend the `cats` list in `write_csvs` for more expenses).
2. **Another markdown**: create the file under `raw/` (**no frontmatter** —
   pure prose; register its metadata + links in `eval/STORY.md` instead).
   Reference it from at least one existing hub file (e.g. `notes/ideas.md`)
   so it joins the graph.
3. **Another thread entirely**: pick a new hobby/career for Wren, add a
   subdirectory under `raw/`, and seed it with at least one
   markdown hub + 2–3 binaries. Update `STORY.md` to mention the new
   thread.

When extending, prefer **specific content** over generic. "Wren's first
paper was rejected by *Limnology and Oceanography* in April 2022" is a
better corpus node than "Wren submitted a paper that was rejected" — the
specifics give retrieval something to anchor to.
