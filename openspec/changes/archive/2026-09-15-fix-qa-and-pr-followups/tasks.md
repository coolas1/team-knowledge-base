## 1. Retrieval correctness (retrieval-relevance)

- [x] 1.1 In `_filter_by_relevance` (`src/engine/hindsight_components/recall.py:605`), drop the `elif` so the semantic floor applies in every mode and the deep-mode rerank gate applies on top (design D1); verify with unit tests: deep-mode candidate with semantic 0.2 + reranker-driven final 0.45 is dropped, above-floor + below-score-gate is dropped, above both is kept, keyword hits pass in all modes
- [x] 1.2 Add `conversation_recall_min_semantic` (default 0.25) to the hindsight options and use it on the conversation-memory recall path only (design D2); verify with a unit test that a 0.3-semantic memory is recalled by memory recall but excluded from public recall, and that the two floors are independently configurable
- [x] 1.3 Update `tests/engine/memory/test_recall.py` so it no longer depends on a keyword hit to clear the gates (assert semantic-only memory recall passes the memory floor); verify `uv run pytest tests/engine/memory/` passes
- [x] 1.4 Rework `tests/frontend/test_bff_agent.py::test_agent_ask`: raise the Acme fixture's semantic score above 0.45 and assert the LLM answer path; add a separate below-gate test asserting the shared not-found constant; verify `uv run pytest tests/frontend/test_bff_agent.py` passes

## 2. Ingest robustness (ingest)

- [x] 2.1 Add a bounded retry helper (exponential backoff + jitter, transient exception classes only) around the analyzer LLM call, the embedder call, and the reindex path, with `engine.ingest.llm_retries` (default 3) and `llm_backoff_base_seconds` (default 2) in `config/app.yaml` (design D3); verify with unit tests using a flaky fake endpoint: transient failures recover, non-transient exceptions are not retried, exhausted budget surfaces the last error
- [x] 2.2 Add a type-first error formatter (`"{type}: {msg}"`, never empty) and use it for the pipeline's failed-status writes; verify with a unit test that a message-less exception (e.g. `asyncio.CancelledError()`) yields a non-empty `error_msg` and the document is marked failed
- [x] 2.3 Add the background-task registry + done-callback to the bare `create_task` sites — `edit_document` (`backend.py:368`) and both `reingest` branches (`:397`, `:400`) — converging the PR #4 P2 and PR #6 N2 fixes (design D4); mark the affected row (the new version, for an edit) failed on unexpected death; verify with unit tests: a task that raises marks the document failed with a non-empty error and logs, a versioned-edit reindex failure marks the new version failed and reingest recovers it, a completing task is discarded from the registry, and cancellation is not reported as failure
- [x] 2.4 Add a code comment at `_ingest_one` (`backend.py:279,288`) documenting the returns-the-task invariant that keeps those sites safe; verify the comment states that callers must hold the returned task reference (registry not required there)
- [x] 2.5 Add the `KB_MAX_UPLOAD_BYTES` setting (default 100 MiB) and bounded reads in the single and batch upload routes (`routes_documents.py:168`, `:187`) returning 413 `file_too_large` (design D7); verify with BFF tests: an oversized single upload is rejected with 413 and no document, and a batch with one oversized file isolates the rejection per-file
- [x] 2.6 Add the OCR quality gate (fail with an actionable message when meaningful text is below threshold) and make the OCR-missing install hint platform-aware (design D11); verify with unit tests: a textless image is marked failed with the OCR message, an adequate image indexes, and the Linux hint names `tesseract-ocr` packages rather than `brew`
- [x] 2.7 Add a regression test asserting `remove()` deletes the `uploads/<id>/` directory (QA follow-up #2, code already present at `backend.py:405/413`); verify the new test passes and fails if the cleanup call is removed
- [x] 2.8 Delete the duplicate `logger = logging.getLogger(__name__)` assignment (`backend.py:55,57`, PR #6 N1); verify `grep -c "logger = logging.getLogger" src/engine/graphrag/backend.py` returns 1 and `uv run ruff check` stays clean

## 3. BFF API fixes (webapp)

- [x] 3.1 Type all document-ID path params (`doc_id: uuid.UUID`) across get/delete/retry/content and PR #6's `versions` / `versions/diff` routes so malformed IDs return 422 (design D6); verify with BFF tests: `GET /api/documents/not-a-uuid` → 422, the same on a version route, a well-formed missing UUID → 404
- [x] 3.2 Make the edit-content path distinguish a missing document (404) from a non-editable type (400, "仅支持 Markdown 文档"); verify with a BFF test asserting 400 + reason on a non-markdown doc
- [x] 3.3 Exclude `mcp` from the SPA fallback in `app.py` (design D11); verify with a BFF test that `GET /mcp` returns a JSON 404, not the SPA shell
- [x] 3.4 Extend the Neo4j `get_related_docs` projection with the edge `relation_type` and render it in the SPA (omitting the parenthetical when absent); verify with a contract test on the response shape and a client test that no empty `()` is rendered
- [x] 3.5 Wire `hops` from `/api/graph/neighbors` through `get_neighbors` (`backend.py:754`) into `neo4j.query_neighbors` and return the links among the result set (design D10); verify with a test asserting the `hops=1` neighborhood and non-empty `links`, and that out-of-range `hops` stays a validation error
- [~] 3.6 Filter conversation-derived content from public read paths (design D5): chunk query + related docs in `_search.py` (alongside PR #6's existing `is_current` filter), public-source filter for graph nodes and `related_entities`; verify with tests that a retained conversation turn's chunks/entities do not surface in public search or the graph while the memory-recall path still returns them
- [x] 3.7 Deduplicate entity relation listings to one entry per (type, direction, endpoint); verify with a test that parallel edges collapse to a single entry

> **Live-check correction (2026-09-10, post-deploy of `6d89918`):**
> 3.1–3.3, 3.5, 3.7 verified against the LAN deployment. **3.4 and 3.6 are
> only half-effective:** with `engine.memory.enabled: true`, `/api/search`
> goes through `HindsightRecallAdapter` and never calls the `_search.py`
> path these two fixes touch — so conversation chunks still appear in search
> results and `related_docs[].relation_type` is always empty (the SPA no
> longer renders `()`, which is why the display bug is gone). The graph half
> of D5 *is* verified live: conversation-junk entities (`assistant`,
> `aa6a3b7b`) no longer resolve. Follow-up: filter in the adapter
> (`src/engine/hindsight_components/compat.py`) — see `docs/todos.md`.

## 4. SPA fixes (webapp)

- [x] 4.1 Add an error state with a 返回 affordance to `DocumentDetailPage` (no eternal spinner, polling stopped on failure) and clear the displayed stale `error_msg` when a retry starts; verify with component tests for both behaviors
- [x] 4.2 Add a catch-all 404 route and page; verify with a component test that an unknown path renders the 404 page with navigation home
- [x] 4.3 Add `nodePointerAreaPaint` (radius `12/globalScale`) to `KnowledgeGraph`; verify with a component test that the prop is passed and paints at least twice the rendered node radius, and `cd src/frontend/webapp/client && npm test` passes
- [x] 4.4 Verify the detail page's pending-poll surfaces `failed` for a versioned edit whose reindex died (PR #6 N2 second half), and pin it with a component test: a document moving `processing` → `failed` under polling renders the failed panel with the error; fix the poll/skip logic if it stops early

## 5. Conversation memory & agent (conversation-memory)

- [x] 5.1 Cap retained turn content in `enqueue_conversation_turn` at `conversation_max_turn_chars` (default 100,000) with a `[truncated]` marker (design D8); verify with a unit test that a 200k-char turn is retained once, bounded, with the marker
- [x] 5.2 In pi-agent, log every swallowed recall/retention failure, report memory-status unavailability as `unavailable` instead of `failed: 1`, and never emit an unclosed `<untrusted_conversation_memory>` tag (design D9); verify with the pi-agent test suite (`cd src/extensions/pi-agent && npm test`)
- [x] 5.3 Wrap `generate_artifact` in `asyncio.to_thread` in the MCP server (`server.py:453`) and include the underlying error in `_conversation_operation_failed` (`:103`); verify with a unit test that the tool does not block the loop (sync artifact function sleeps; concurrent coroutine completes) and the failure message names the cause
- [x] 5.4 Extract the not-found answer string into one shared constant used by `reflect.py` and both skills (design D11); verify with `uv run pytest` (existing assertions updated to the constant) and grep showing a single definition

## 6. Build & CICD (container-build, local-cicd)

- [ ] 6.1 Make the Aliyun PyPI mirror remap in the Containerfile opt-in via `ARG PYPI_MIRROR` (default: upstream, design D12); verify with a local `podman build` without the arg succeeding with un-remapped `uv.lock` URLs
- [ ] 6.2 Pass the mirror build arg from `cicd/pipeline.sh` for LAN builds; verify the pipeline's build stage logs the mirror in use on the next deploy
- [ ] 6.3 Add `cicd/backup.sh` (pg_dump + uploads volume snapshot into `<stable-dir>/backups/`, keep-last-5) invoked by `pipeline.sh` before each redeploy, with restore steps in `cicd/README.md`; verify after the next pipeline deploy that a dated backup pair exists in the stable directory and a restore drill recovers a document + its original file

> **§6 status (2026-09-10):** all three implementations landed and passed the
> static checks available off-deployment — the remap simulation leaves a
> no-arg build on upstream URLs (`pypi.org` 107, `mirrors.aliyun.com` 0) and
> remaps 1474 URLs with the arg; `podman compose config` renders
> `PYPI_MIRROR=""` by default and the Aliyun mirror when set; `backup.sh`
> passes `bash -n` and is wired before `stage_deploy`. The verification
> clauses that need a real build/deploy (local `podman build`, next-deploy
> build log, backup presence + restore drill) run with the first pipeline
> deploy — tracked alongside 8.2.

## 7. Docs bookkeeping

- [x] 7.1 Record the PR #6 follow-ups in `docs/upstream-pr-followups.md` (new PR #6 section: N1 duplicate logger, N2 versioned fire-and-forget reindex — both checked off as landed) alongside checking off the PR #3 P1–P3, PR #4 P1–P3/P5/P6, and cross-PR items, leaving PR #4 P4 and repo-level CI unchecked; then delete the `docs/pr6-followups.md` scratch doc per its own graduation note; verify the tracked doc reflects exactly what this change shipped and the scratch doc is gone
- [x] 7.2 Document `KB_MAX_UPLOAD_BYTES` and the new `engine.ingest` retry knobs in `.env.example` / `config/app.yaml` comments; verify a fresh reader can discover every new knob from those files alone

## 8. Validation & deployment

- [x] 8.1 Run the full validity check: `uv run ruff check`, `uv run pytest`, and `cd src/frontend/webapp/client && npm test` — all green before pushing
- [ ] 8.2 After the pipeline deploys, verify on the LAN deployment: malformed doc ID → 422 with error state (no spinner), oversized upload → 413, `GET /mcp` → JSON 404, unknown SPA route → 404 page, search results free of "Conversation turn" chunks with relation types shown, entity panel deduped, `hops=1` neighbors honored, a versioned edit produces a new indexed version with the old one retired, and a bulk re-upload batch indexes without operator intervention
- [ ] 8.3 Data remediation (QA open questions 2–3): run `bench/qa-webapp-2026-09-09/harness/resubmit.sh` for the remaining ~18 pre-cutover text docs, confirm all indexed, then delete the 27 stale 09-03 rows (including the 2 without raw counterparts); verify search no longer returns duplicate chunks and no doc dead-ends on edit/retry
- [ ] 8.4 Re-run the 15-question bench QA eval against the redeployed service to confirm the stricter deep-mode gates hold answer quality (mean ≥ 0.9, vs 0.93 on 2026-09-03)

> **§8 status (2026-09-10):** 8.1 verified green — ruff clean, `uv run pytest`
> 349 passed / 5 skipped, SPA `npm test` 33 passed (Node 20.20.2), pi-agent
> 92 passed (Node 24.21.0). 8.2–8.4 are post-deploy/operations tasks: they
> start once this change is committed, pushed, and picked up by the LAN
> pipeline. 8.3 deletes 27 rows and resubmits ~18 documents — destructive, and
> not yet authorized against the live deployment.
