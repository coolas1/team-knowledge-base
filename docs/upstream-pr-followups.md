# Upstream PR follow-ups (PR #3 & PR #4)

Outstanding issues from the reviews of PR #3 (recall relevance threshold
gate) and PR #4 (conversation memory + document workflows). Both PRs were
accepted as-is, merged upstream, and re-mapped onto the three-module layout
(merge `f979e858`, 2026-09-03) — none of the items below are fixed yet.
Paths use the current layout. Check items off as they land.

## PR #3 — recall relevance threshold gate

- [ ] **P1 — Deep-mode gate logic contradicts its own documentation.**
  In `_filter_by_relevance` (`src/engine/hindsight_components/recall.py`), the `elif`
  makes the semantic and score gates mutually exclusive: in deep mode with
  a reranker score present, only `final_score >= recall_min_score` (0.4)
  applies. Because `final_score` is clamped to
  `semantic + rerank_semantic_margin` (0.25), the effective semantic floor
  in deep mode is **0.15**, vs the 0.45 the config comment and PR
  description claim ("semantic gate applies to every mode"). Worked
  example: semantic 0.2 + hallucinated reranker 0.95 →
  final = min(0.95, 0.45) = 0.45 ≥ 0.4 → kept, while fast mode drops it.
  Fix either direction, but pick one consciously:
  - apply both gates (drop the `elif`, check semantic first, then the
    score gate in deep mode) — matches the docs; or
  - keep the behavior and fix the config comment to say deep mode trusts
    the reranker within the clamp margin.
- [ ] **P2 — BFF happy-path test was repurposed instead of extended.**
  `tests/tkb/test_bff_agent.py::test_agent_ask` asserts the not-found
  string for "where is Acme?", so the found-chunks → LLM-answers path has
  no BFF-level coverage for `/api/agent/ask` (a sibling test covers a
  different endpoint), and the test freezes a threshold false-positive as
  expected behavior (the Acme fixture scores below the 0.45 gate). Fix:
  bump the fixture's semantic score above the gate, restore the
  happy-path assertion, and add a separate not-found test with
  below-gate fixtures.
- [ ] **P3 — Not-found string hardcoded in three places.**
  `"知识库中未找到与该问题相关的内容。"` appears in
  `src/engine/hindsight_components/reflect.py`, `src/agent/tkb/skills/search_and_answer/skill.py`,
  and `src/agent/tkb/skills/reflective_search/skill.py`. Extract a shared
  constant.
- [ ] **P4 — Containerfile bakes the Aliyun PyPI mirror into the shared
  build.** `sed`-remapping `uv.lock` URLs to `mirrors.aliyun.com` and the
  pinned astral.sh uv installer (0.12.5) make every environment's image
  build depend on those hosts. Make the mirror opt-in (build arg /
  deployment overlay) instead of unconditional.

## PR #4 — conversation memory + document workflows

- [ ] **P1 — No transcript size bound on retention (design promised one).**
  `design.md` lists "bound transcript size" as the mitigation for LLM
  extraction cost, but `enqueue_conversation_turn`
  (`src/engine/hindsight_components/conversation_service.py`) validates only
  non-emptiness — no cap on the engine side, the queue, or the pi-agent
  runtime. A pasted ~200k-char document becomes one retained Document,
  ~50+ chunks, each hitting the LLM extractor, per turn, with the feature
  enabled by default in Compose. Fix: cap turn content in
  `enqueue_conversation_turn` (e.g. 50–100k chars), truncate with a
  marker; optionally cap client-side in `src/extensions/pi-agent/src/runtime.ts`
  too. (Compare: `src/agent/artifacts.py` already caps content at 250k.)
- [ ] **P2 — Fire-and-forget reindex tasks can die silently.**
  `edit_content` / `reingest` in the graphrag backend (plus the
  pre-existing `ingest` site — four total) schedule
  `asyncio.create_task(...)` with no reference held and no exception
  handling: tasks can be garbage-collected mid-flight and failures
  vanish, leaving documents stuck at `status="pending"` forever. Fix:
  keep strong references (task registry) and add a done-callback that
  logs and marks the document `failed` on error.
- [ ] **P3 — `generate_document` blocks the event loop.**
  The MCP tool calls synchronous `generate_artifact`
  (docx/pdf/pptx via reportlab/python-pptx, up to 250k chars) inside the
  async handler; the deployment runs BFF + engine + plugin in one
  process, so a large generation stalls all requests. Fix: wrap in
  `asyncio.to_thread` in `src/agent/tkb/mcp/server.py`.
- [ ] **P4 — Artifacts have no retention policy.**
  Generated files accumulate forever in the `artifactsdata` volume. Add a
  TTL or max-size sweep (e.g. delete artifacts older than N days on
  worker poll).
- [ ] **P5 — Silent failure swallowing where the design promises
  diagnostics.** `recallMemoryForPrompt` and `enqueueCompletedTurn`
  (`src/extensions/pi-agent/src/conversation-memory.ts`, `runtime.ts`) catch all
  errors with no logging — recall outages are indistinguishable from "no
  memories". `health()` also fabricates `failed: 1` when the status call
  errors, conflating "queue has a failed job" with "status unavailable".
  Fix: one log line per swallowed failure; report status-unavailable
  explicitly.
- [ ] **P6 — Minor cleanups.**
  - `_conversation_operation_failed(operation, error)` ignores its
    `error` parameter (MCP server module).
  - `formatConversationMemoryBlock` tiny-budget fallback can emit an
    unclosed `<untrusted_conversation_memory>` tag.
  - `_recall_source_conditions` adds an `EXISTS` subquery to every recall
    arm in every mode, even with conversation memory disabled —
    negligible but on the hottest path; consider gating when the feature
    is off.
  - Uploads still have no request-size cap (pre-existing; the upload
    route and the frontend's 413 suggestion were touched by PR #4 — add
    a BFF-level cap while in there).

## Cross-PR

- [ ] **Conversation recall is subject to PR #3's relevance gates.**
  `recall_conversation_memory` rides the shared recall path, so the
  `recall_min_semantic=0.45` gate filters conversation memories too
  (BM25 hits pass). Hidden until now because the old layout's
  `testpaths=["tests"]` never collected `src/**/tests/`. Decide whether
  conversation recall should bypass or use separate thresholds; the test
  in `tests/engine/memory/test_recall.py` currently relies on a keyword
  hit to pass the gates.

## Repo-level

- [ ] **No CI on the repository.** Both PRs' "tests pass" claims were
  author-reported only. Add at least `uv run ruff check` +
  `uv run pytest` on PRs (unit/contract/BFF tests pass without live
  services), plus the two npm suites for touched paths.
