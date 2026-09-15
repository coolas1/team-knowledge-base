# Design: fix-qa-and-pr-followups

## Context

See `proposal.md` — Why. The relevant current-state facts that shape this
design (verified on `main` at `b1bfd43a`, i.e. post PR #6 "versioned
documents"):

- The LAN deployment runs BFF + engine + plugin in **one process**, against a
  locally hosted vLLM backend (~60 tok/s) — ingest latency is expected, but
  transient endpoint saturation is not handled at all today.
- Recall gating lives in `_filter_by_relevance`
  (`src/engine/hindsight_components/recall.py:605`); the config comment and
  the PR #3 description still document the intended "both gates" behavior —
  only the code disagrees.
- Conversation-derived documents are already distinguishable
  (`is_public_document` / `INTERNAL_DOCUMENT_FILE_TYPES`,
  `src/engine/components/store/models.py:73`) and the edit/retry/remove paths
  already use that distinction — the search and graph read paths do not.
- PR #6 introduced version chains: `edit_document` commits the old version's
  demotion (`is_current=False`) and then fire-and-forgets the new version's
  reindex; `list_documents` and the recall SQL already filter to
  `is_current=True` (`_search.py:77`) — so a dead reindex task makes the
  whole version group vanish from retrieval while the new row sits at
  `pending` forever.
- The bare `create_task` sites on current `main` are `edit_document`
  (`backend.py:368`) and the two `reingest` branches (`:397`, `:400`). The
  four ingest sites recorded in the PR #4 review were consolidated into
  `_ingest_one` (`:279`, `:288`), which **returns** the task — callers hold
  the reference, so those are already safe. A duplicate
  `logger = logging.getLogger(__name__)` survives at `backend.py:55,57`.
- `remove()` already deletes `uploads/<id>/` (`backend.py:405`, cleanup at
  `:413`) — the QA follow-up "verify" is answered; this change only adds a
  regression test.
- There is no backup mechanism in `cicd/` at all; the pre-cutover data loss
  was an unbacked uploads volume.
- The pipeline is the sole operator of the deployment; all fixes ship through
  it, and the QA data remediation is sequenced after the ingest retry fix.

## Goals / Non-Goals

**Goals:**

- Every fix is independently testable at unit/contract level (no live
  services required), per the repo's validity check.
- Error paths become observable and actionable (non-empty `error_msg`,
  logged swallowed failures, honest health status).
- No successful-path API shape changes — additions and error-code changes
  only. PR #6's version semantics are preserved; we only harden its failure
  paths.

**Non-Goals** (beyond the proposal's out-of-scope list):

- No DB schema migrations; all fixes work against the current schema (PR #6's
  idempotent migrations applied automatically by the pipeline deploy).
- No changes to concurrency defaults (`doc_concurrency=2`,
  `chunk_concurrency=4`) — retry/backoff is the chosen mitigation for
  endpoint saturation; lowering defaults punishes faster backends and
  remains a deployment knob.
- No new client-side truncation in pi-agent (engine-side cap suffices; the
  PR note marked it optional).
- No redesign of PR #6's demote-then-reindex ordering (see D13).

## Decisions

### D1 — Recall gates: apply both, matching the documentation
In `_filter_by_relevance` (`recall.py:605`), drop the `elif`: check the
semantic floor (`recall_min_semantic`, 0.45) for every non-keyword candidate,
then apply the deep-mode rerank-score gate (`recall_min_score`, 0.4) on top
when a reranker score exists.
*Alternative rejected*: keeping the behavior and fixing the config comment —
that would legitimize an effective 0.15 semantic floor whenever a reranker
hallucinates a high score, which is exactly the failure PR #3's gate existed
to prevent; the documented intent (config comment, PR description) is the
both-gates semantics.

### D2 — Conversation-memory recall: separate, lower floor (not a bypass)
Add `conversation_recall_min_semantic` (default 0.25) to the hindsight
options, used by the conversation-memory recall path in place of the public
`recall_min_semantic`. Keyword hits keep bypassing it.
*Alternatives rejected*: full bypass (stale or irrelevant memories would flow
into every prompt — the injection budget is small but not free) and reusing
the public floor (status quo; only keyword hits survive, which is why the
bug stayed hidden — the old test collection never ran `src/**/tests/`).

### D3 — Ingest robustness: retry at the model-call boundary, not the document boundary
Wrap the analyzer's LLM call and the embedder call in a shared bounded
retry helper (exponential backoff with jitter; retry only transient classes:
timeouts, connection errors, HTTP 429/5xx). Config knobs under
`engine.ingest` (`llm_retries`, default 3; `llm_backoff_base_seconds`,
default 2, capped). Re-running a whole document on failure was rejected: it
re-pays extraction and risks partial-state complexity; per-call retry is
where the observed transient timeouts actually occur. This also covers the
reindex calls that PR #6 fire-and-forgets.
Empty `error_msg`: format terminal errors as `"{type}: {msg}"` with the
exception type always present, so message-less exceptions (observed with
cancellation-style failures under saturation) still identify the failure.

### D4 — Background tasks: registry + done-callback covering the versioned edit/reindex path (PR #4 P2 + PR #6 N2, converged)
A module-level `set[asyncio.Task]` (add on schedule, discard in a
done-callback) for the bare `create_task` sites: `edit_document`
(`backend.py:368`) and both `reingest` branches (`:397`, `:400`). The
callback logs unexpected exceptions and best-effort marks the affected
document row — the **new version row** in the edit case — `failed` with the
formatted error (D3 formatter). `CancelledError` is logged at info and not
treated as failure. `_ingest_one` (`:279`, `:288`) is explicitly out of
scope: it returns the task and callers hold the reference; a code comment
documents that invariant so refactors don't silently reintroduce a bare
site.
The SPA half of N2: verify the detail page's pending-poll already surfaces
`failed` for the stuck new version (it polls `pending`/`processing` and
renders the failed-doc panel on `failed`) and pin it with a component test.
The old pre-PR-#6 site list (`163/183/208/211`) no longer exists — the
ingest sites were folded into `_ingest_one` by PR #6.

### D13 — Versioned-reindex failure policy: surface, don't roll back
When a versioned edit's reindex fails, we mark the new version `failed`
(D4) and leave the version chain as PR #6 designed it (old version demoted,
excluded from retrieval). The document is then absent from default retrieval
until the user retries (the failed-doc panel's 重新处理 → `reingest`).
*Alternatives considered and deferred*: (a) flip `is_current` only after a
successful reindex (the new row would need a non-current incubation state);
(b) restore the previous version to `is_current=True` in the done-callback.
Both change PR #6's version-lifecycle semantics — version numbers, diff
routes, and the upload-dedupe logic (`find_version_candidate`) all assume
demote-on-save — and go beyond a bugfix. With D3 retries making reindex
failures rare and D4 making them visible with one-click recovery, the
residual window (failed edit temporarily unsearchable) is acceptable; if it
bites, the promote-on-success redesign is its own change.

### D5 — Public/private content split on read paths
- Search (`_search.py`): the chunk query joins `documents` and filters
  `file_type NOT IN INTERNAL_DOCUMENT_FILE_TYPES`, alongside PR #6's
  existing `is_current=True` filter (`:77`) — one more condition on the same
  statement, not a second mechanism; `related_docs` likewise.
- Graph view: nodes are filtered to those with at least one public source
  document (at the Neo4j query level where cheap, else post-filter);
  `related_entities` in search results gets the same filter.
- The conversation-memory recall path must not acquire this filter — it goes
  through the hindsight repository, not `_search.py`; a task verifies this
  and pins it with a test (this is also the D2 regression risk).

### D6 — Doc ID validation: typed path params
Change `doc_id: str` to `doc_id: uuid.UUID` on the document routes
(`get`, `delete`, `retry`, `content`, and PR #6's `versions` /
`versions/diff`); FastAPI then returns 422 for malformed values before
storage is touched, and well-formed misses still 404. Manual parsing was
rejected as more code for the same contract.

### D7 — Upload cap: bounded read in the route, settings-driven
Read at most `max_upload_bytes + 1` in the single and batch upload routes
(`routes_documents.py:168`, `:187`; spooled read, not full buffering);
over-cap → 413 `file_too_large` with the limit in the message. The limit is
a pydantic setting (env `KB_MAX_UPLOAD_BYTES`, default 100 MiB). A
uvicorn/app-level middleware cap was rejected: it would also cap
MCP/artifact traffic that legitimately differs, and the route-level check
makes batch per-file isolation natural.

### D8 — Transcript cap: engine-side, configurable
`enqueue_conversation_turn` truncates combined turn content to
`conversation_max_turn_chars` (hindsight option, default 100,000) with a
trailing `[truncated]` marker, before enqueueing. Mirrors the existing
250k cap pattern in `src/agent/artifacts.py`.

### D9 — pi-agent diagnostics: log at every swallow; honest unavailable status
`recallMemoryForPrompt` / `enqueueCompletedTurn` catches get one
`console.warn` each with the operation and error. The status-poll catch
reports `{enabled: true, unavailable: true, …zeroed counts}` instead of
fabricating `failed: 1`. `formatConversationMemoryBlock` returns `""` when
the budget cannot fit opening + one line + closing, so the
`<untrusted_conversation_memory>` tag is never left unclosed.
`_conversation_operation_failed` (MCP server) appends
`"{type(error).__name__}: {error}"`.

### D10 — Neighbors endpoint: wire hops, populate links
Thread `hops` from the route through `get_neighbors` (`backend.py:754`,
hardcodes `hops=2` / `links: []` on current main) into
`neo4j.query_neighbors`, and extend that query to return the relationships
among the result set so `GraphData.links` is populated. Removing the dead
parameter was rejected: the API contract already advertises `hops` and the
graph panel is the natural future consumer; removal would be a breaking
cleanup with no defect behind it.

### D11 — Small fixes, chosen shapes
- `related_docs[].relation_type`: extend the Neo4j `get_related_docs`
  projection (`_search.py:215`) with the edge type; SPA falls back to
  omitting the parenthetical when absent.
- Relation dedupe: collapse to one entry per (type, direction, endpoint) in
  the entity-detail assembly.
- OCR quality gate: after extraction, if meaningful text (alphanumeric+CJK
  chars) is below a small constant threshold, raise an extraction error
  ("OCR 未提取到有效文本…") so the document fails with an actionable message.
- OCR install hint: `sys.platform` check — darwin keeps `brew`, everything
  else names `tesseract-ocr` / `tesseract-ocr-chi-sim` (apt) packages.
- `PUT …/content` on non-markdown: distinguish missing (404) from
  non-editable (400, "仅支持 Markdown 文档") in the edit-content path.
- `/mcp` fallback: add `mcp` to the SPA-fallback exclusion prefixes
  (`app.py`), keeping the JSON 404.
- SPA: `DocumentDetailPage` gains an error state (message + 返回 link,
  polling stopped; the eternal `加载中...` is at `:160`); retry clears the
  displayed stale `error_msg`; catch-all `*` route renders a 404 page;
  `KnowledgeGraph` adds `nodePointerAreaPaint` at `12/globalScale`.
- Not-found constant: one shared constant (engine-side module both layers
  already import) replacing the three copies.
- BFF happy-path test: raise the Acme fixture's semantic score above 0.45,
  assert the LLM answer; add a separate below-gate not-found test asserting
  the shared constant.
- PR #6 N1: delete the duplicate `logger` assignment at `backend.py:55/57`
  (pure cleanup, no behavior).

### D12 — Build & CICD
- Containerfile: `ARG PYPI_MIRROR=""`; the `sed` remap and
  `UV_DEFAULT_INDEX` are applied only when the arg is non-empty;
  `cicd/pipeline.sh` passes the Aliyun mirror for LAN builds. The digest-
  pinned uv image from ghcr stays (it is a registry pull like any base
  image, not an installer-script dependency).
- Backup: new `cicd/backup.sh` — `pg_dump` via `podman exec` and an uploads
  volume snapshot (podman volume export) into
  `<stable-dir>/backups/<sha>-<ts>/`, keep-last-5; invoked from
  `pipeline.sh` immediately before the redeploy replaces the stack; restore
  steps documented in `cicd/README.md`.

## Risks / Trade-offs

- [Both-gates fix reduces deep-mode recall volume] → intended correctness
  change; re-run the 15-question bench eval after deploy to confirm answer
  quality holds (harness exists from 2026-09-03).
- [Retry/backoff lengthens wall-time per failing doc] → bounded budget
  (default ≈ 3 retries, ≤ ~30 s backoff each); a doc that still fails now
  fails with a real error instead of an empty one.
- [Graph view loses conversation-only nodes (count drops)] → intended
  (those are the junk entities QA flagged); memory recall is unaffected
  (D5 verification task).
- [Failed versioned edit leaves the version group unsearchable until retry]
  → accepted per D13: failure is visible with one-click recovery, and D3
  makes reindex failures rare; promote-on-success remains the follow-up if
  it bites.
- [100 MiB upload cap could reject a legitimate huge file] → configurable
  via env; error message names the limit.
- [Per-redeploy backups cost time/disk] → bounded retention; dump sizes are
  modest at current corpus scale; acceptable on a 5-min poll that only
  deploys on new commits.
- [Backup script runs `podman` against the deployment] → must follow the
  single-operator rule: invoked only by the pipeline, never by hand in the
  dev checkout (documented in the script header).

## Migration Plan

1. Implement + land through the pipeline (lint → tests → build → deploy);
   no new schema migration (PR #6's migrations already applied by the
   `b1bfd43a` deploy).
2. Post-deploy verification on LAN (scripted, per QA harness pattern):
   malformed-ID 422, oversized upload 413, `GET /mcp` 404, search shows no
   "Conversation turn" chunks, related docs show relation types, entity
   panel deduped, neighbors `hops=1` honored, versioned edit → new version
   indexed and old version retired.
3. Data remediation (QA open questions 2–3): bulk-run
   `bench/qa-webapp-2026-09-09/harness/resubmit.sh` for the remaining ~18
   pre-cutover text docs (now self-healing thanks to D3), verify all
   indexed, then delete the 27 stale 09-03 rows; the 2 docs without raw
   counterparts are deleted as broken-for-edit/retry either way.
4. Rollback: `git revert` on `main`; the pipeline redeploys the prior SHA
  (images retained per local-cicd spec). No data migration to undo; the
  resubmitted corpus is valid under either version.

## Open Questions

- OCR quality-gate threshold value: start with a conservative constant
  (tunable later against the bench image corpus; the 10 known-good OCR docs
  from QA must keep indexing).
- Whether conversation memory should also be excluded from agent answer
  citations in `/ask` (it flows through the same recall adapter) — expected
  to be resolved by D5 naturally, verified during post-deploy QA.
