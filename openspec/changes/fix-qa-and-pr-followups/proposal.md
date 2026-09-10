# Proposal: fix-qa-and-pr-followups

## Why

Two independent reviews left a backlog of confirmed, unfixed defects on current
`main`: the 2026-09-09 web QA pass (`docs/qa-report-2026-09-09-webapp.md`) found
a 500 + eternal spinner on malformed doc IDs, transient bulk-ingest failures
with empty `error_msg`, conversation transcripts leaking into public search
results, and a string of smaller API/SPA defects; and the upstream PR review
follow-ups (`docs/upstream-pr-followups.md`, PR #3/#4) list gate logic that
contradicts its own documentation, unbounded transcript retention,
fire-and-forget tasks that can die silently, and an event-loop-blocking MCP
tool. None of these are fixed yet, and the QA data-remediation (bulk resubmit
of the pre-cutover corpus) is explicitly blocked on the ingest retry fix.

PR #6 (versioned documents, author wwwttlll) was re-reviewed, accepted, and
merged as `b1bfd43a` on 2026-09-09. Its two non-blocking review follow-ups
(`docs/pr6-followups.md`, scratch doc) are folded into this change, and all
anchors below are re-verified against post-PR-#6 `main`. Notably, PR #6
consolidated the four bare `create_task` ingest sites into `_ingest_one`
(which returns the task — already safe) but added new bare sites in the
versioned edit/reingest path, so the PR #4 P2 item and the PR #6 N2 item
converge on one fix.

## What Changes

**BFF / API (webapp)**
- Malformed doc IDs are rejected as 422 at the route instead of 500 from the
  asyncpg UUID cast; missing-but-well-formed IDs stay 404. Applies to every
  document route, including PR #6's new `/{doc_id}/versions` and
  `/{doc_id}/versions/diff`.
- Doc detail page gets an error state with a back affordance instead of the
  eternal `加载中...` spinner.
- Upload endpoints enforce a configurable size cap (default 100 MiB) and
  return 413 — the client's existing 413 handling becomes reachable.
- `GET /mcp` no longer serves the SPA shell (excluded from fallback like
  `/api/*`); unknown SPA routes render a 404 page instead of a blank screen.
- `/api/search` `related_docs` entries include `relation_type` (currently
  rendered as empty parens in the SPA).
- Conversation-derived content (memory chunks, junk entities from transcript
  ingestion) is excluded from public search results and the public graph view.
- `/api/graph/neighbors` honors its `hops` parameter and returns links instead
  of hardcoding `hops=2` / `links: []`; entity relations are deduplicated
  (no parallel-edge repeats in the entity panel).
- `PUT /api/documents/{id}/content` on a non-markdown doc returns 400 with a
  clear reason instead of a misleading 404 `文档不存在`.
- Failed-doc panel shows only the latest error (stale `error_msg` cleared on
  retry); graph nodes get a usable pointer hit area (`nodePointerAreaPaint`).

**Engine pipeline (ingest robustness)**
- Transient LLM/embedding failures during ingest are retried with bounded
  exponential backoff, so sustained load (observed 11/25 failures against a
  ~60 tok/s vLLM backend) self-heals instead of failing the document.
- A failed document always carries a non-empty `error_msg` (today
  cancellation-type exceptions surface as `""`).
- Background reindex tasks hold strong references and mark the affected
  document `failed` on unexpected death — no more silent GC or eternal
  `pending`. Post-PR-#6 the bare `create_task` sites are `edit_document`
  (demotes the old version, then fire-and-forgets the new version's reindex)
  and the two `reingest` branches; a failed versioned reindex leaves the new
  version `failed` with a visible error in the doc detail UI (PR #6 N2, same
  umbrella fix as PR #4 P2).
- A duplicate `logger = logging.getLogger(__name__)` assignment in
  `backend.py` (survivor of PR #6's review fix) is removed (PR #6 N1).
- OCR quality gate: images whose OCR yield is effectively empty fail with an
  actionable message instead of being indexed as garbage documents.
- The OCR-not-installed hint is platform-aware (no `brew install` on Linux).

**Retrieval correctness (PR #3 + cross-PR)**
- Deep-mode relevance gating applies both the semantic floor and the rerank
  score gate (the `elif` today makes them mutually exclusive, dropping the
  effective floor to 0.15 vs the documented 0.45).
- Conversation-memory recall gets its own, lower semantic threshold so the
  public-corpus gate (0.45) doesn't gut memory recall (currently only keyword
  hits survive).

**Conversation memory (PR #4)**
- Retained turn content is capped (truncate with a marker) — a pasted
  ~200k-char document no longer becomes 50+ LLM-extracted chunks per turn.
- pi-agent logs swallowed recall/enqueue failures and reports
  status-unavailable honestly instead of fabricating `failed: 1`; the memory
  block always closes its `<untrusted_conversation_memory>` tag.
- MCP conversation-memory failures include the underlying error; the
  `EXISTS` recall subquery is gated on the memory feature flag.
- The not-found answer string is extracted to one shared constant (three
  copies today).

**Build / CICD**
- Containerfile's Aliyun PyPI mirror remap becomes opt-in via build arg
  (default: upstream PyPI); the CICD build passes the mirror.
- CICD gains a backup step covering Postgres and the uploads named volume
  (the pre-cutover data loss was exactly an unbacked volume).

**Tests / data remediation**
- The repurposed BFF happy-path test is restored (above-gate fixture → LLM
  answer) with a separate below-gate not-found test.
- After deploy: bulk-resubmit the remaining ~18 pre-cutover text docs
  (`resubmit.sh`), then delete the 27 stale 09-03 rows.

**Explicitly out of scope** (recorded, not silently dropped): repo-level CI
(repo-level item in follow-ups), artifacts TTL retention (PR #4 P4), entity
name-splitting at spaces (needs its own change with eval), auto-restoring or
re-promoting the previous version when a versioned reindex fails (changes
PR #6's demote-then-reindex semantics; see design for the deferred
alternatives), and the QA "Recommendations beyond fixes" list (E2E suite,
click-throughs, sort/page options, per-file progress, zoom buttons). Session
list staying global is accepted as-is for a single-team tool.

## Capabilities

### New Capabilities
- `webapp`: BFF HTTP API + SPA user-facing behavior — request validation,
  error states, search/graph response shape, and conversation-content exclusion.
- `retrieval-relevance`: relevance gating of recall results — semantic floor
  in every mode, deep-mode rerank gate, keyword bypass, memory-recall
  threshold.

### Modified Capabilities
- `ingest`: transient-failure retry/backoff, non-empty failure errors,
  background-task lifecycle (including versioned edit/reindex), upload size
  cap, OCR quality gate.
- `conversation-memory`: bounded turn retention, memory-recall gate
  decoupling, honest failure diagnostics from the client runtime.
- `container-build`: upstream package indexes by default, mirror opt-in.
- `local-cicd`: deployment backups cover the database and uploads volume.

## Impact

- **Code:** `src/engine/graphrag/{backend,pipeline,_search}.py`
  (task-registry sites: `edit_document` / `reingest`; duplicate logger at
  `backend.py:55,57`), `src/engine/components/extractors/image.py`,
  `src/engine/hindsight_components/{recall,conversation_service,reflect,repository}.py`
  (gate now at `recall.py:605` post-PR-#6),
  `src/agent/tkb/mcp/server.py`, `src/agent/tkb/skills/**`,
  `src/extensions/pi-agent/src/{conversation-memory,runtime}.ts`,
  `src/frontend/webapp/server/{app,routes_documents,routes_graph,routes_search}.py`,
  `src/frontend/webapp/client/src/**` (pages/components).
- **Config:** `config/app.yaml` (retry knobs), Containerfile build args,
  `cicd/pipeline.sh` (mirror arg, backup step) + new `cicd/backup.sh`.
- **APIs:** two response-shape additions (`related_docs[].relation_type`,
  neighbors `links`), new 422/413/400 error cases — additive or
  error-path-only; no breaking changes to successful-path shapes; PR #6's
  version endpoints and semantics are preserved (only error-path hardening).
- **Tests:** `tests/frontend/test_bff_agent.py` rework; new unit tests per
  fix (PR #6 merge gate baseline: 312 passed / 5 skipped); live
  re-verification on the LAN deployment (pipeline-managed).
- **Data:** post-deploy deletion of 27 stale 09-03 document rows after
  resubmission completes (operational, gated on verification).
