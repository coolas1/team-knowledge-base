# Web UI QA Report — 2026-09-09

Manual QA pass over the LAN deployment (`http://localhost:8000`, pipeline-managed
compose, current `main`). Tested with headless Chrome 153 driven by scripted
Playwright (`/tmp/kb-qa`, scenarios s01–s08) with console/network capture on
every action, cross-checked against BFF/engine source and raw API responses.
Artifacts (screenshots + per-scenario event logs + harness scripts):
`bench/qa-webapp-2026-09-09/` (git-ignored).

Corpus at test time: 38 docs (27 indexed, 10 failed images, 1 conversation
doc); graph 1252 nodes / 2208 links.

## Verdict

Core flows are solid: upload (single + batch + error isolation), live pipeline
progress, edit + re-ingest, delete with confirm, filters/pagination, search,
agent chat with SSE streaming/citations/stop, session restore, graph load
performance. No uncaught JS errors in any scenario. The notable issues are one
operational data-loss problem (pre-cutover uploads), a 500 on malformed doc
IDs, a search contract mismatch rendering empty `()` in the UI, and several
data-quality/UX items below.

## What works (verified end-to-end)

| Area | Result |
|---|---|
| Home list, type/status filters, pagination | pdf=5, image=10 counts correct; prev disabled on p1; filter resets page |
| Upload single .md | navigates to detail, pipeline stages live (提取文本→…→写入知识图谱), indexed in ~64 s, summary auto-generated |
| Upload batch (2 good + 1 csv) | partial success isolated; error banner for csv only; both good docs indexed |
| Upload errors | `.csv` → 400 `unsupported_file_type` banner with format list + 重新选择 (no retry, correct); empty file → 400 `empty_file` |
| Edit markdown | editor prefilled, save → re-ingest, summary regenerated with new content |
| Delete | confirm-dismiss keeps doc; confirm-accept deletes + navigates home |
| Nonexistent doc ID | clean 404 + alert (but see B2b) |
| Failed-doc panel | error msg + hint + 重新处理 button; inline retry error with suggestion |
| Search | blank/whitespace submit is a no-op client-side (0 requests); real query → 16 chunks, entities, docs; Enter key works |
| Ask | session restore after reload; suggestions prefill only; SSE streaming with status line (正在连接 Agent → 调用 tkb_* → 回答完成 · 调用工具 2 次); citations link to docs; stop button mid-stream works (已停止); session rename after first message; delete confirm cancel/ok both correct |
| Graph | loads 1252 nodes in 3.4 s, zero console errors; entity filter, zoom, pan OK; node click opens full panel (描述/来源文档/关联关系); background click closes |
| SPA fallback | deep links (`/search`, `/ask`) survive reload; `/api/*` misses stay JSON 404; `/health` ok |
| Agent answer quality | DHW 7.8 (95% CI 7.3–8.3) answer correct vs corpus, well-cited |

## Bugs

### P1 — pre-cutover uploads lost: edit/retry permanently broken for the 27 pre-cutover docs

`retry` on a pre-volume doc returns `原始文件不存在，请重新上传文件`. Root cause:
uploads named volume (`e2c6e783`) was introduced after these docs were ingested
on 09-03; DB rows survived the cutover, upload files did not. Every pre-cutover
doc (not just the failed images) now dead-ends on retry, and markdown edit will
hit the same wall when the pipeline needs the original.

**Remediation during this QA (2026-09-09):**
- The 10 failed images were resubmitted from `benchmark/raw/` — all 10
  indexed; the 10 stale failed rows (09-03) were deleted (failed docs: 0).
- Bulk re-upload of the 25 text docs (22 md + 3 pdf) failed 11/25 under
  concurrent load (see P2 below); a serial re-run indexed 6 + 1 in-flight
  (≈5 min/doc) before being stopped as too slow.
- Agreed next step: fix the pipeline retry/backoff issue first, then
  bulk-submit the remaining ~18 files and let the pipeline self-heal. The
  idempotent resubmission script (`resubmit.sh`, skips already-done titles) is
  in `bench/qa-webapp-2026-09-09/harness/`.
- 13 csv/xlsx files in `benchmark/raw/` are not uploadable by design (BFF
  allowlist excludes them).

Follow-ups: (1) include the uploads volume in backups; (2) `remove()` doesn't
clean `uploads/<id>/` — verify; (3) the 27 old 09-03 rows still exist with
lost files (edit/retry dead-end) and now duplicate the resubmitted content —
delete them once all replacements are indexed (see Open questions).

### P1 — malformed doc ID → HTTP 500 + page stuck on "加载中..."

`GET /api/documents/not-a-uuid` → 500 (asyncpg UUID cast error) instead of
422/404. UI: alert "加载失败: Internal Server Error", then the page stays on
加载中... forever — `loadDoc` catches but leaves `doc=null`
(`DocumentDetailPage.tsx:86-88,147`). Needs: input validation at the route +
an error state with a back affordance instead of the eternal spinner.

### P2 — search renders `标题 ()`: `relation_type` never sent by the API

`/api/search` `related_docs` entries contain only `doc_id` + `title`; the SPA
renders `{d.title} ({d.relation_type})` (`SearchPage.tsx:54`) → every related
doc shows dangling empty parens. Either return the relation type or drop it
from the UI.

### P2 — ingest can fail with empty error_msg under sustained load

During bulk re-upload of the corpus (25 docs, 2 s apart, `doc_concurrency=2`),
**11 of 25 failed** — same content that indexed fine on 09-03 — and all show
`status=failed` with **empty `error_msg`**, so the UI panel renders
"文件处理失败" with no detail. Failures were concentrated in the
heaviest/longest docs. Two issues: (1) pipeline error handling loses the
exception message; (2) sustained ingest load (LLM/embedding endpoints) causes
failures with no retry/backoff — consistent with the Hindsight degradation
observed during the ask test (see Performance). A serial re-run indexed the
same files one-at-a-time successfully, and one first-attempt failure was
recovered by a single retry — the failures are transient timeouts; backoff +
retry in the pipeline would make bulk ingest self-healing.

### P2 — agent chat transcripts indexed into the searchable corpus

"Conversation turn" chunks (raw `[user] … [assistant] …` text, including a
stale doc-count summary) surface as regular search results and in ask
citations. At minimum confusing (looks like a broken doc titled
"Conversation turn"); also a mild information-leak vector if the KB is ever
exposed wider. Consider: filter memory chunks from `/api/search` default
results, or badge/deduplicate them in the UI.

### P2 — graph node hit targets are ~6px

`nodeCanvasObject` draws r = 6/globalScale but no `nodePointerAreaPaint` is
supplied (`KnowledgeGraph.tsx:107-138`), so pointer detection uses the default
tiny radius. With 1252 nodes, clicking a specific entity is genuinely hard
(40+ precise clicks missed before one landed). Fix: paint a larger pointer
area (e.g. r = 12/globalScale). Would also help: zoom buttons/reset, min/max
zoom clamp.

### P2 — entity extraction quality

- Names split at spaces: `红曲霉（Monascus` and `purpureus）` exist as two
  separate graph nodes.
- Junk entities from conversation ingestion: `assistant`, doc-id fragments
  (`aa6a3b7b`), path segments (`fermentation/`) — these pollute the graph and
  search's 相关实体 list.
- Duplicate relation rows in the entity panel (`← SAME_AS: koji-kin` twice) —
  parallel edges not deduped.

### P3 — assorted

1. **`hops` param dead + no links in neighbors API**: `/api/graph/neighbors`
   accepts `hops` but never passes it (`routes_graph.py:24-30`), backend
   hardcodes `hops=2` and always returns `links: []` (`backend.py:280-286`).
   Unused by the SPA; fix or remove.
2. **Unknown routes render a blank page** — no catch-all route; console only
   logs `No routes matched location "/no-such-page"`. Add a 404 page.
3. **`GET /mcp` serves SPA HTML** — SPA fallback doesn't exclude `/mcp` for
   GET (`app.py:82-93`). MCP itself works (agent sidecar used it all session).
4. **OCR error suggests `brew install`** on a Linux/podman deployment
   (`Tesseract OCR 未安装。请运行: brew install tesseract tesseract-lang`).
5. **Failed-doc panel stacks errors** — after a failed retry both the stale
   `error_msg` and the new inline retry error show simultaneously.
6. **No server-side upload size limit** — `file.read()` unbounded
   (`routes_documents.py:142`); the client's 413 handling is unreachable.
   Add a cap (uvicorn/app-level) before this is exposed beyond the LAN.
7. **OCR'd photos become garbage-indexed docs** — e.g. a screenshot ingested as
   `CO BME ine Qe 2iil BABRAB.` with a confident summary. Consider a quality
   gate (skip/flag when OCR text is below a threshold); textless photos add
   noise, not signal.
8. **`PUT /api/documents/{id}/content` on non-markdown** returns 404
   `文档不存在` (misleading; button is hidden in UI so unreachable from SPA).
9. **Session list is global** — all users share one conversation list; fine
   for a single-team tool, worth remembering before wider exposure.

## Performance observations

- **LLM backend context:** the deployment uses a locally hosted Qwen via
  vLLM (coolas-3, ~60 tok/s, no API cost — intentional for CICD). Ingest
  latency of 1–4 min/doc is therefore expected, not a defect.
- Small .md ingest ≈ 64 s end-to-end; large md earlier ≈ 13 min (03:01→03:14).
  10 image uploads ingested in background without errors (images are
  LLM-light).
- **Bulk ingest is the exception — it fails:** uploading 25 text docs 2 s
  apart (`doc_concurrency=2`, `chunk_concurrency=4`) yielded **11/25 failed,
  14/25 indexed** in ~75 min. Every failure was among the heaviest docs (both
  PDFs, lab-notebook, itinerary + CJK-heavy mds), and all with empty
  `error_msg` (see P2 above). Single/serial uploads of the same content
  succeed. Conclusion: concurrent pipeline load saturates the vLLM endpoint
  until per-call timeouts kill the pipeline, and the failure path loses the
  error message. Needs: pipeline-level backoff/retry, lower default
  concurrency vs a 60 tok/s backend, and error propagation into `error_msg`.
- Agent answer ≈ 2 min (2 tool calls); during concurrent image ingest the
  answer noted **Hindsight degradation** (`query_analysis_llm` and
  `neural_rerank_llm` timeouts) and fell back to fast search — degradation is
  surfaced to the user in the answer text, which is good, but indicates the
  same saturation.
- `/api/graph/full` (1252 nodes/2208 links) renders in ~3.4 s, no console
  errors; search returns in ~1–5 s.

## Not tested / limited coverage

- Upload button disabled state during an in-flight upload (race too short to
  observe reliably); oversized-file behavior (no server cap to trigger 413).
- pi-agent down path (503 banner) — sidecar was healthy all session.
- Mobile layout (≤760 px ask sidebar overlay); `/api/query` (hindsight POST),
  artifacts download/slidev routes (no SPA callers).

## Recommendations beyond fixes

1. The SPA has **no component/page tests** (only `client.test.ts`); this QA
   pass was manual. The scripted harness in `/tmp/kb-qa` (lib.js + scenarios)
   is a starting point for a Playwright E2E suite against a seeded corpus.
2. Search results: link 相关文档/相关实体 to doc detail / graph nodes;
   chunks currently have no click-through either.
3. Render `memory_status`/`memory_count` in doc detail (fields exist in the
   API, nothing consumes them).
4. Batch upload: per-file progress — a 2-file batch spins the header button
   for minutes with no feedback beyond "上传中".
5. Doc list: sort options and page-size selector; currently newest-first only.

## Open questions

1. ~~Delete the 10 redundant failed image rows?~~ — done 2026-09-09.
2. ~~Resubmit the pre-cutover corpus?~~ — images + 7/25 text docs done;
   remainder to be bulk-submitted after the pipeline retry fix (see P1).
3. Delete the 27 old 09-03 rows once all replacements are indexed? Until
   then, search returns duplicate chunks and old rows dead-end on edit/retry.
   (Note: 2 of the 27 have no counterpart in `benchmark/raw/` — they would
   stay broken for edit/retry either way.)
