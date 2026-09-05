## Why

`tkb_search_deep` currently gives its MCP operation the same 300-second deadline as the entire Pi Agent turn, while each internal Hindsight LLM request may wait 600 seconds. An intermittently slow model request therefore consumes the whole turn, prevents answer synthesis, and produces too little diagnostic evidence to identify the stalled phase.

## What Changes

- Give Hindsight deep recall a bounded total deadline and shorter per-phase deadlines for query analysis, embedding, retrieval arms, and LLM reranking.
- Treat optional deep-search phases as degradable: return useful partial evidence with explicit degradation metadata when analysis, graph, temporal, or reranking phases fail or time out.
- Keep cancellation authoritative and distinguish user cancellation, phase timeout, total search timeout, upstream failure, and successful degraded results.
- Reserve time in every Pi turn for fallback and answer synthesis; validate that the deep-tool deadline cannot consume the complete turn and make tool timeouts aware of the turn's remaining time.
- Permit one bounded `tkb_search_fast` fallback after a deep-search timeout or unusable degraded result, without opening an unbounded retry loop.
- Add correlated phase timing and outcome logs that remain available on failed and cancelled requests, while preserving the existing successful `trace.phase_ms` response.
- Replace the Python-side full-table BM25 scan with bounded indexed PostgreSQL keyword retrieval suitable for Chinese and English content, and cap the candidate text sent to LLM reranking.
- Change deployment defaults from a 300-second deep tool inside a 300-second turn to a 60-second deep tool inside a 180-second turn, with documented sizing rules.

## Capabilities

### New Capabilities

- `deep-search-resilience`: Defines bounded deep-search execution, graceful partial-result degradation, turn-level deadline coordination, observable failures, bounded fallback, and scalable keyword retrieval.

### Modified Capabilities

None. The repository currently has no main OpenSpec capability covering deep search.

## Impact

- Pi adapter configuration, execution budgeting, `tkb_search_deep` orchestration, tool errors, and prompt guidance under `src/extensions/pi-agent/`.
- Hindsight recall/provider/repository code and MCP result/error behavior under `src/engine/`.
- PostgreSQL schema/index initialization and a migration-safe index rollout for existing data.
- Compose and environment examples for new phase deadlines and corrected outer timeout defaults.
- Engine, Pi SDK-loop, MCP integration, cancellation, degraded-result, scale, and observability tests.
- No change to the public `tkb_search_deep(query, top_k)` input schema; successful responses gain additive degradation/diagnostic fields.
