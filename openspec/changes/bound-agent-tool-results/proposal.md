## Why

Agent turns can build unbounded contexts from tool payloads. On 2026-09-11 a
second ask turn (session `01a090b6`) fetched a whole paper through
`tkb_get_document` (full `raw_text`, `backend.py:911`) plus a 44-item
`tkb_list_documents` page (`page_size` uncapped → ~23K chars) and a degraded
deep-search result, growing the conversation to **42,926 tokens**. The LLM
backend then returned a degenerate 1-token empty response (turn failed
`agent_failed`) and the retry wedged to the 180s cap (`time_limit`). Any turn
that reads one long document can reproduce this on the current backend.

## What Changes

- **Bound `tkb_get_document`**: stop returning full `raw_text` in one
  response. Return metadata + overview + chunk stats plus a bounded window of
  the text, with explicit continuation parameters (offset/limit style) so the
  model pages through long documents deliberately.
- **Clamp `tkb_list_documents` `page_size`**: server-side maximum (default
  stays 20); oversized requests are clamped and the effective value is
  reported in the response.
- **Cap deep-search evidence payloads**: bound per-source excerpt length and
  the total evidence budget of `tkb_search_deep` results (trimming to a
  reported total), so degraded or broad searches cannot dump their full
  corpus into the conversation.
- Payload-size contract tests for all three tools.

Non-goals: LLM backend capacity (infrastructure — recommendations in
`bench/qa-ask-latency-2026-09-11/REPORT.md`, git-ignored); the agent turn
time budget (separate change `extend-agent-turn-budget`); context compaction
inside the pi runtime.

## Capabilities

### New Capabilities

- `knowledge-tools`: payload bounds for the `tkb_*` MCP tools the agent
  consumes — the tools SHALL return bounded responses so a single turn cannot
  assemble an unbounded context from tool output. No existing spec covers the
  tool payload contract.

### Modified Capabilities

(none)

## Impact

- **Code:** `src/agent/tkb/mcp/server.py` (tool parameter schemas and
  shaping), `src/engine/graphrag/backend.py` (windowed raw-text access,
  `page_size` clamp, list shaping), engine deep-search result shaping. The
  BFF REST routes (`routes_documents.py`) must keep their current full-detail
  behavior — bounding happens at the MCP tool layer or behind explicit
  parameters, not by changing `KnowledgeBase` defaults the BFF shares.
- **Compatibility:** the `tkb_get_document` MCP response shape changes for
  the agent (content becomes windowed). The pi sidecar surfaces tool output
  verbatim, so no sidecar code change is needed; its prompts/skill docs that
  describe the tool should be updated to teach windowed reading.
- **Validation:** `uv run pytest` (new payload-size contract tests);
  `RUN_INTEGRATION=1` for live tool-shape verification; post-release, replay
  the incident question on the LAN deployment.
- **Risk:** low-medium — reads become multi-step for long documents (more,
  smaller tool calls), trading latency for survivable contexts.
