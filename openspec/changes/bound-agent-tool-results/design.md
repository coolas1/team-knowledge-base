## Context

See proposal.md — Why. The three payload paths the incident exercised:
`tkb_get_document` returns the full `raw_text` column (`backend.py:911`);
`tkb_list_documents` passes `page_size` straight to SQL with no clamp
(`backend.py:876`, overview already capped at 200 chars/item);
`tkb_search_deep` returns the deep-search pipeline's evidence set untrimmed.
The BFF REST routes (`routes_documents.py:67,72`) call the same
`KnowledgeBase` methods the MCP tools wrap, so engine-level defaults must not
change REST behavior. The pi sidecar surfaces tool output verbatim into the
model context; nothing today bounds what lands there.

## Goals / Non-Goals

**Goals:**

- Every `tkb_*` read tool returns a response whose payload is bounded by
  configuration, with in-band continuation so the agent can still read
  everything — more, smaller steps instead of one unbounded dump.
- REST API behavior unchanged.
- Bounds visible in tests (payload-size contract tests), not just constants.

**Non-Goals:**

- LLM backend capacity (infra; QA report recommendations stand).
- Agent turn time budget (`extend-agent-turn-budget`).
- Token-level accounting — bounds are characters, not tokens; cheap and
  backend-agnostic.

## Decisions

1. **Bound at the MCP tool layer, not in `KnowledgeBase`.** The MCP wrappers
   in `src/agent/tkb/mcp/server.py` clamp and shape; `backend.py` gains only
   an opt-in windowed-text accessor. REST routes keep calling the unshaped
   methods. This keeps one backend, two presentations.
   - *Clamping inside `KnowledgeBase.list_documents`*: rejected — it would
     silently change `GET /api/documents` for REST consumers.
2. **`tkb_get_document` windowing.** Tool gains `offset`/`limit` params
   (defaults: offset 0, limit = configured window, e.g. 8,000 chars).
   Response keeps metadata/overview/chunk stats, replaces `raw_text` with
   `text_window`, `offset`, `total_chars`, `has_more`. The backend accessor
   slices server-side (SQL `substr` or Python slice of the fetched row) —
   start with the Python slice for simplicity; the column is already loaded.
3. **`tkb_list_documents` clamp.** Tool layer clamps `page_size` to a
   configured max (default 50) and echoes the effective value (it already
   returns `page_size` — it will now report the clamped one).
4. **Deep-search evidence budget.** Trim in the engine's deep-result shaping,
   ranked order preserved (lowest-ranked evidence dropped first), per-excerpt
   cap plus total budget (e.g. 2,000 / 12,000 chars), recorded in the trace
   (`evidence_trimmed: true` + counts) next to the existing `degraded`
   marker, so the sidecar's `pi.deep_search` telemetry stays truthful.
5. **Configuration.** Budgets as settings under a new `engine.tools` section
   in `config/app.yaml` (`window_chars`, `list_page_max`,
   `deep_excerpt_chars`, `deep_total_chars`), flowing through
   `config/settings.py` like every other knob; constants only as fallback
   defaults.
6. **Agent-side awareness.** Update the sidecar's tool descriptions/skill
   docs that describe `tkb_get_document` to teach windowed reading (the
   requirements demand self-describing responses; descriptions make the
   model use them well).

## Risks / Trade-offs

- [Long documents now need several calls] → more round-trips per turn; each
  iteration is a cheap prefill instead of one 43K-token monster. On the
  current backend this is strictly better for turn survival.
- [Model misuses windowing (re-reads from 0)] → self-describing responses +
  tool descriptions mitigate; watch post-release QA.
- [Deep-search trimming hides evidence] → trace marker + ranked trimming
  keep the highest-value evidence; `top_k` still controls breadth.

## Migration Plan

Frontend/engine change behind existing config plumbing; no data or wire
migration. Rollback = revert + redeploy. Post-release verification: replay
the incident question (session `01a090b6` turn 2) on the LAN deployment and
confirm the turn completes without hitting the context or time limits.
