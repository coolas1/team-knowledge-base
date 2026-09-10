# Issues — verified working vs. still open

Snapshot from the 2026-09-03 live run: native backend on `main` @ `4218e140`
(BFF+engine+plugin in-process), Postgres+Neo4j via podman, pi-agent container,
Ollama `nomic-embed-text` @10.201.186.15, LLM `deepseek-v4-flash`,
reranker disabled (`none` — see R1). Corpus: `benchmark/raw/` (48 files);
QA eval results in `bench/qa/RESULTS.md` (15/40, mean 0.93).
The unchecked items in the upstream PR follow-up ledger (retired to git
history 2026-09-10 — `git show 6d899180:docs/upstream-pr-followups.md`) were
all open at the time of this snapshot; this file adds what the live run
verified and what it newly surfaced. Superseded items and the current
outstanding list live in `docs/todos.md`.

## Verified working (live evidence, not just unit tests)

| Area | Commits | Evidence |
| ---- | ------- | -------- |
| PR #3 — recall relevance gate | `6323149c` | Gate is live on the recall path: `/api/search` and `/api/query` both ride it; BM25 bypass observed (kw>0 chunks retained); unit tests pass in the 307-green suite. |
| PR #4 — indexed-file edit (was 405) | `c3293534` | End-to-end probe: PUT `/api/documents/{id}/content` on an **indexed** doc → 200 → pending→processing→indexed → new content top hit in search. Pre-fix 405 is gone. |
| Three-module restructure | `64cf4a98` | Native `uvicorn src.frontend.webapp.server.app:app` boots and served the whole run; `uv run pytest` → **307 passed, 7 skipped, 0 failed**; engine/plugin/tkb cross-imports all exercised. |
| Parallel ingest | `c5c6e789`…`4218e140` | Live integration tests: `test_batch_entities_match_single_upserts` ✅, `test_ingest_batch_roundtrip` ✅ (13 s). Corpus ingest visibly paired docs at the same second (`doc_concurrency: 2`); BFF batch upload endpoint live-tested. |

## Open issues

### New — found by the live run

- **N1 · P1 — Relevance-gate floor is below the embedding noise floor.**
  `recall_min_semantic=0.45` but unrelated text scores ≈ 0.49–0.53 under
  `nomic-embed-text`; a gibberish query ("quantum hedgehog taxonomy zephyr
  midnight") returns 5 unrelated chunks on both `/api/search` and
  `/api/query`. The gate mechanism works, but its default can't achieve
  PR #3's stated goal ("queries without KB coverage return not found").
  Fix: calibrate the threshold per embedding model (measure the
  unrelated-pair distribution, or normalize scores), or gate on a
  reranker/noop-margin instead of raw cosine.
- **N2 · P1 — HttpReranker fails closed.** A dead token (current
  llm-api.net key returns 401 "无效的令牌") makes `rerank()` raise and takes
  down every search. Degrade to noop (log loudly) instead of 500ing; also
  rotate the key.
- **N3 · P1 — BFF upload allowlist ≠ extractor registry.** The BFF rejects
  `.csv` ("不支持 .csv 文件") while `markdown.py`'s
  `SUPPORTED_EXTENSIONS` includes `.csv`; `.xlsx` has no extractor at all.
  13/48 benchmark files (10 csv + 3 xlsx) never entered the KB, capping ~11
  of the 40 QA questions by construction. Fix: add xlsx extractor
  (openpyxl), align the BFF list with the registry (single source of truth).
- **N4 · P2 — Image "multi-modal" is OCR-only and broken natively.**
  `ImageExtractor` is pytesseract (`chi_sim+eng`); the host has no tesseract
  → 10/10 images failed ingest (Containerfile installs it, native runs
  don't). Even with OCR, non-text visuals (the benchmark's procedural
  gradient/waveform cover) are unretrievable — needs a vision path for
  real M coverage. (A ready conda env sits at `/tmp/tess-env`, unwired.)
- **N5 · P2 — ja→en cross-lingual retrieval gap.** A Japanese query never
  surfaced the English equipment file (zh→zh worked fine). Q37 answered
  only after an English re-query.
- **N6 · P3 — Chunking blind spot for frontmatter/blockquotes.** The paper
  mirror's canonical "Submitted to *Coral Reefs* 2024-09-12" blockquote was
  never retrievable by chunk search; only the journal corroborator was (Q18
  scored 0.75). Consider metadata-aware chunking or frontmatter indexing.
- **N7 · P3 — `test_parallel_pipeline_indexes_document` is flaky by design.**
  `len(matches)==1` assumes the LLM analyzer assigns one entity type per
  name; live it produced 3 types (Organization/Probe/Facility) → 3 nodes.
  MERGE `(label,name)` itself is correct (one label merged 4 sources).
  Loosen the assertion (group by name) or normalize types at write time.
- **N8 · P3 — MCP server rejects non-localhost Host headers.** The
  streamable-HTTP DNS-rebind protection breaks container→host wiring
  (`host.containers.internal` → "Invalid Host header"); pi-agent needs
  `--network host` + `TKB_MCP_URL=http://localhost:8000/mcp/`. Document it
  or make allowed hosts configurable.
- **N9 · P3 — `benchmark/ingest.sh` targets a dead endpoint.** Posts to
  `/api/engine/ingest` on :8002; the current BFF exposes
  `/api/documents/upload[/batch]` on :8000. Update the script.
- **N10 · P2 — Deployment drift.** The prebuilt `team-kb-webapp:latest`
  image is 7 days / 10 commits behind `main` (predates PR #3/#4 remap and
  parallel ingest); a `podman compose up --build` rebuild took long enough
  to be abandoned. Cache the heavy layers or publish images on merge.

### Carried over — `docs/upstream-pr-followups.md` (all still open)

- PR #3: P1 deep-mode `elif` gate contradiction (confirmed still present at
  `src/engine/hindsight_components/recall.py:267-270` — deep mode with a reranker score
  skips the semantic gate entirely; note N1 makes the semantics matter even
  more); P2 repurposed BFF happy-path test; P3 not-found string ×3; P4
  Aliyun mirror baked into the Containerfile.
- PR #4: P1 no transcript-size bound on retention; P2 fire-and-forget
  reindex tasks (still `asyncio.create_task` at `backend.py:160,180,205,208`
  — no reference held, no failure handling; the edit-fix probe worked but
  the silent-death mode remains); P3 `generate_document` blocks the event
  loop; P4 artifacts retention; P5 silent error swallowing; P6 minors
  (incl. no upload size cap).
- Cross-PR: conversation recall inherits PR #3's gates (now compounded by
  N1 — mis-calibrated floors filter conversation memories too, or don't
  filter at all).
- Repo-level: no CI; tonight's "307 passed" is again author-reported.

## Suggested order of attack

1. N2 (reranker degrade) + key rotation — unblocks default-config search.
2. N3 (csv/xlsx ingest) — biggest benchmark coverage win, small change.
3. N1 + PR#3-P1 together — the gate's semantics need one coherent decision.
4. N4 (tesseract/vision) — restores all image questions.
5. N7 test fix, N8/N9/N10 docs-and-plumbing.
