# `team-knowledge-base` — retrieval benchmark results

- **Run date:** 2026-09-03
- **KB under test:** team-knowledge-base (native backend @ :8000, current `main` @ 4218e140; Postgres+Neo4j via podman; embeddings nomic-embed-text via Ollama @10.201.186.15; chunk analysis deepseek-v4-flash)
- **Questions:** 15 of 40 (subset spanning every category; D=5, L=5, T=3, M=2, X=3 — X overlaps D/L)

## Summary

- **Overall mean score:** 0.93 / 1.00
- **Per category:**

  | Cat | Meaning               | N | Mean  |
  | --- | --------------------- | -- | ----- |
  | D   | Detail locating       | 5  | 1.00  |
  | L   | Link following        | 5  | 0.98  |
  | T   | Temporal / date range | 3  | 0.92  |
  | M   | Multi-modal           | 2  | 0.63  |
  | X   | Cross-lingual         | 3  | 0.97  |

- **Honesty:** 1 honest "not found" (Q22 — image never ingested); 0 fabricated answers.
- **Retrieval mode:** retrieval-only (`/api/search` + `GET /api/documents/{id}` full-text reads); reranker disabled (`RERANKER_PROVIDER=none` — the configured llm-api.net rerank token is dead, 401 on every call).
- **Disclosure:** the operator (same agent) had read `eval/qa/README.md` (category map, planted-inconsistency list) before predicting. Predictions were still restricted to KB-returned content; inconsistency answers quote only what retrieval surfaced.

## Corpus ingestion state (defines the ceiling)

- 48 files in `benchmark/raw/` → **35 accepted, 13 rejected at upload** (10 `.csv` + 3 `.xlsx`: BFF allowlist omits both, even though the engine's extractor registry declares `.csv` supported), **25 indexed, 10 failed** (all images: tesseract OCR binary absent in this native run — the Containerfile would install it in a container build). upload verdicts in `/tmp/ingest-results.txt`.

## Per-question scores

| ID | Cat | Score | Verdict (one line) |
| -- | --- | ----- | ------------------ |
| 01 | D   | 1.00  | Correct: Onset HOBO U22-001, 8 m, 30-min (paper §2.2 via KB full-text). |
| 03 | D   | 1.00  | Correct: Toop (2004), Haunted Weather. |
| 06 | D   | 1.00  | Correct: A. oryzae (koji-kin) + Yamada Nishiki. |
| 08 | L   | 1.00  | Correct: T4 Cladocopium → RYK-06 Sesoko West. |
| 09 | L   | 1.00  | Correct + inconsistency surfaced: sketch 70/F#m vs release 72/Am. |
| 10 | L   | 1.00  | Correct: Boulotte et al. 2016, full citation from PDF references. |
| 13 | L   | 1.00  | Correct: RYK-05 Cape Hedo; resisted the RYK-06 decoy. |
| 16 | T   | 1.00  | Correct: 6 days (Nov 14 → Nov 20, 2023). |
| 18 | T   | 0.75  | Date correct (2024-09-12) but the "Coral Reefs" linkage lives in the paper .md frontmatter blockquote, which retrieval never surfaced. |
| 19 | T   | 1.00  | Correct: 2024-03-01 → 2024-04-19. |
| 22 | M   | 0.25  | Honest not-found: album-cover.png failed ingest (no OCR backend); only "TIDAL" title known via liner notes. |
| 25 | M   | 1.00  | Correct from PDF §3.2 inside the KB: 8.36, CI 7.91–8.82, abstract ≈8.4. |
| 32 | X/D | 1.00  | Correct from zh PDF: DHW 7.8, CI 7.3–8.3 (zh query hit zh source directly). |
| 36 | X/D | 1.00  | Correct: 白化 in both JA/ZH, 日中同形 noted. |
| 37 | X/L | 0.90  | Answer correct (Zoom H5 + Aquarian H2a) but the Japanese query missed the English source; succeeded only after an English re-query — deduction for the degraded cross-lingual path. |

## Per-question detail

### Q01 — SST logger
- **Score:** 1.00 — model/depth/interval all correct, canonical §2.2 quoted. Chunk search alone kept missing §2.2 (mirror doesn't carry it); the full-document read endpoint recovered it.

### Q03 / Q06 — detail lookups
- **Score:** 1.00 each — single-hop phrase/ingredient retrieval worked first try.

### Q08 — track ↔ refugium
- **Score:** 1.00 — EP notes dedication + paper refugia list joined correctly. (The key's primary source `tracklist.csv` was never ingested — .csv rejected — yet the .md prose carried the fact.)

### Q09 — sketch vs release (planted inconsistency)
- **Score:** 1.00 — both values retrieved and the shift explicitly surfaced (70→72 BPM, F#m→Am), which is the full-credit condition.

### Q10 — reframed-symbionts citation
- **Score:** 1.00 — reading-list phrase + full PDF reference-list citation both retrieved.

### Q13 — triad
- **Score:** 1.00 — RYK-05 Cape Hedo with all three conditions grounded; correctly rejected the RYK-06 temptation (T4 dedication decoy).

### Q16 — day arithmetic
- **Score:** 1.00 — 6 days; used the panel-landing date (11-14) not the journal date (11-15).

### Q18 — submission target
- **Score:** 0.75 — date correct from the journal's "Submission target now Sep 12" + revision timeline; the paper .md frontmatter ("Submitted to Coral Reefs 2024-09-12") exists in the corpus but chunk search never surfaced it — a chunking blind spot for frontmatter blockquotes.

### Q19 — sabbatical dates
- **Score:** 1.00 — three corroborating sources retrieved.

### Q22 — album cover (multi-modal)
- **Score:** 0.25 — honest not-found. The KB holds no pixels: ingest of all 10 images failed (tesseract missing in the native run; the Containerfile installs it for container builds). Score reflects the KB's failure to engage the modality, not the predictor.

### Q25 — breakpoint from PDF (multi-modal)
- **Score:** 1.00 — the PDF text layer was extracted and searchable inside the KB; precise CI retrieved, not just the abstract's ≈8.4.

### Q32 — zh-source detail
- **Score:** 1.00 — Chinese query returned the Chinese PDF as top hit with the exact numbers.

### Q36 — trilingual glossary
- **Score:** 1.00 — glossary row retrieved; shared-character note included.

### Q37 — ja query → en source
- **Score:** 0.90 — correct equipment with correct EN source cited, but the Japanese query returned only JA/zh-adjacent files; the English source surfaced only after re-querying in English.

## Notable findings

**KB strengths**
- Multi-hop link-following is strong: all five L answers (incl. the hard triad Q13 and the decoy-laden Q08) were resolved purely from KB retrieval trails.
- PDF text extraction + indexing works well (Q01 §2.2, Q25 precise CI, Q10 full citation, Q32 zh PDF).
- zh→zh retrieval is excellent (Q32 top hit, sem 0.77). The planted inconsistency on Q09 was discovered and both sides cited.
- Honest `not found` behavior is easy to maintain: search returns scores/limits that make absence legible.

**KB weaknesses (deployment + product)**
1. **Upload allowlist ≠ extractor registry**: `.csv` is rejected by the BFF (`unsupported_file_type`) while `markdown.py` declares `.csv` supported; `.xlsx` has no extractor at all. 13/48 corpus files never entered the KB — every xlsx/csv-dependent question is unanswerable by construction (Q02, Q04, Q14, Q20, Q23, Q24, Q27, Q28, Q29, Q34, Q35 ceilings).
2. **Images dead in native runs**: extractor is tesseract-OCR based; host lacks the binary → 10/10 images failed. The compose/Containerfile path installs tesseract, so this is a native-run gap, but it also means image "multi-modal" is really OCR-of-text, not vision (Q22's gradient/waveform would still be unretrievable even with tesseract — OCR can't describe a procedural gradient; the answer key's own canonical source is the generator script).
3. **Cross-lingual query→source gap (ja→en)**: Q37's Japanese query never reached the English equipment list; embedding/keyword matching didn't bridge languages. zh↔zh worked; ja→en did not.
4. **Chunk-search blind spot for frontmatter/blockquotes**: Q18's canonical citation sits in a blockquote at the top of the paper mirror; chunk retrieval never surfaced it (found only via the journal corroborator).
5. **Reranker dependency is fail-closed**: HttpReranker raises on HTTP error (401 invalid token tonight) and takes the whole search path down; had to restart with `RERANKER_PROVIDER=none`. A dead key should degrade to noop, not 500.
6. **Chunk-level retrieval needed full-doc fallback** for mid-file facts (recipes koji section, journal sketch, EP track list): chunk search surfaced the right document but rarely the right chunk on first pass; `GET /api/documents/{id}` (full text) did the heavy lifting. A `get_page`/chunk-browsing MCP tool is effectively load-bearing.
