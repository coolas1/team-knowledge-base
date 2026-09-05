## Context

See `proposal.md` for motivation. `tkb_search_deep` maps to Hindsight recall with `mode="deep"`; it does not use the configurable GraphRAG reranker even when `RERANKER_PROVIDER=none`. Deep recall currently performs query analysis and embedding concurrently, four retrieval arms concurrently, then an LLM rerank and entity-state load. Both Hindsight LLM calls inherit a 600-second provider timeout, while the Pi MCP call and whole turn each default to 300 seconds.

Successful recall already returns `trace.phase_ms`, but a stalled request has no persisted phase trace. Optional LLM failures are caught only after their long provider timeout expires. Keyword retrieval currently materializes every active memory and computes CJK-bigram/English-word BM25 in Python; the observed deployment has 2,817 memory units and spends roughly 0.7 seconds in this path, so it will grow linearly.

## Goals / Non-Goals

**Goals:**

- Make one deep retrieval complete, degrade, or fail within a predictable service budget.
- Preserve useful evidence when optional enrichment is slow.
- Keep cancellation distinct from timeout and stop all descendant work.
- Guarantee time for a bounded fallback and final model answer.
- Retain keyword semantics while bounding database and Python work as memory count grows.
- Make historical timeouts attributable to a phase without exposing query or evidence data.

**Non-Goals:**

- Turning deep retrieval into a background job or streaming partial evidence protocol.
- Guaranteeing that every generated answer completes within a fixed wall time after evidence is returned.
- Changing the public search input schema or replacing Hindsight retrieval.
- Introducing a new search service or language-specific tokenizer dependency.
- Treating a degraded result as equivalent to a fully enriched result.

## Decisions

### 1. Use one monotonic deadline budget with bounded child phases

Introduce runtime-neutral deep-search deadline settings and a small budget object based on the event loop's monotonic clock. Every child phase receives `min(configured_phase_timeout, remaining_total_time)`. Recommended defaults are:

| Budget | Default |
| --- | ---: |
| Hindsight deep total | 45s |
| query analysis LLM | 8s |
| query embedding | 10s |
| each retrieval arm | 5s |
| neural rerank LLM | 12s |

The total is not the sum: concurrent phases share elapsed time, and later phases receive only the remaining total. Provider calls must receive these phase values rather than the generic 600-second default. `asyncio.timeout` surrounds the await as a final enforcement boundary, including providers that ignore their supplied timeout.

Alternative considered: only increase Pi's 300-second limit. This leaves the nested 600-second waits and makes failures slower. Another alternative is independent phase timers without a total deadline; that permits sequential phases to exceed the user-facing budget.

### 2. Represent phase outcomes and degrade only optional work

The recall coordinator records an internal outcome for each phase: `succeeded`, `empty`, `timed_out`, `failed`, `cancelled`, or `skipped`, plus elapsed time and a sanitized category. It accumulates candidates as arms complete rather than waiting for one all-or-nothing `gather` result.

- Query-analysis failure yields empty entities and time bounds. Semantic and keyword retrieval continue.
- Embedding failure removes only the semantic arm; keyword and any analysis-derived graph/temporal arms continue.
- Graph or temporal failure removes that arm.
- LLM rerank failure retains deterministic RRF scores.
- If the total deadline fires after useful candidates exist, selection completes over those candidates and returns a degraded result.
- If no usable arm succeeds, the MCP layer returns a typed `deep_search_timeout` or `deep_search_unavailable`, distinct from a successful empty result.

`CancelledError` is never caught by degradation handlers. The coordinator cancels and awaits every pending task before returning or raising, so database sessions and HTTP clients close before the MCP operation completes.

Alternative considered: immediately fall back to fast mode inside the engine. Keeping engine degradation separate from Pi fallback makes trace semantics clear and avoids repeating semantic/keyword work when partial candidates already exist.

### 3. Coordinate Pi deadlines with an explicit synthesis reserve

Add `PI_AGENT_TURN_RESERVE_SECONDS`, default 60. Change default `TKB_DEEP_TOOL_TIMEOUT_MS` to 60,000 and `PI_AGENT_MAX_RUN_SECONDS` to 180. Configuration loading rejects:

```text
deep_tool_timeout_ms + turn_reserve_ms >= max_run_ms
```

At the start of a turn, Pi records a monotonic turn deadline. Tool execution computes an effective deadline from the configured tool timeout and `turn_deadline - reserve - now`; it refuses to start when none remains. This prevents a late tool call from acquiring a fresh full timeout. The existing turn abort remains the outer safety boundary.

Alternative considered: silently clamp invalid static configuration. Failing startup exposes operational mistakes instead of creating deployment-dependent timing behavior.

### 4. Put the single fast fallback in the trusted Pi deep-tool wrapper

The wrapper recognizes typed engine timeout/unavailable results and successful degraded results without usable sources. If sufficient pre-reserve time remains, it calls `search_knowledge_fast` exactly once using the same abort signal and remaining budget. It returns the fast evidence with additive metadata such as `fallback_from="deep"`, while SSE activity identifies the fallback. It never falls back after user cancellation.

The fallback remains within the original Pi tool invocation but increments a dedicated per-turn search-attempt counter; this counter prevents model retries or repeated wrapper entry from cycling deep/fast indefinitely. The general tool-call limit and turn deadline remain authoritative.

Alternative considered: prompt the model to choose fast after an error. That is nondeterministic and has already shown retry-loop risk. A fully hidden engine fallback would make the evidence path harder for Pi and operators to interpret.

### 5. Add failure-path telemetry without content logging

Create a correlation ID at the MCP deep-search boundary and carry it through recall. Emit structured logs for phase start/end and the terminal outcome. Fields are limited to correlation ID, phase name, outcome, elapsed milliseconds, candidate/result counts, timeout configuration, degradation categories, fallback occurrence, and provider class/name. Query text, model payloads/responses, chunks, credentials, and document titles/IDs are excluded by default.

Successful response trace gains additive `search_id`, `outcome`, `degraded`, `degraded_phases`, `phase_outcomes`, `fallback`, and rerank truncation fields while keeping existing `phase_ms`. Typed MCP errors include the correlation ID and failure category so a client-visible failure can be matched to logs.

Alternative considered: rely only on response traces. Those disappear on timeout and cannot diagnose the incident this change addresses.

### 6. Use a GIN-indexed token array as bounded BM25 preselection

Add a normalized lexical-token array to each memory unit using the existing tokenizer: case-folded English words and CJK bigrams. Add a GIN index and dual-write tokens whenever memories are inserted or replaced. Keyword retrieval first applies indexed array overlap, computes an overlap-based database ordering, and limits rows to a configurable candidate cap (default 300). Python BM25 then scores only those candidates, preserving the current scoring behavior over a bounded corpus.

Use an expand/backfill/switch rollout for existing databases:

1. Add the nullable/default-empty column and GIN index without changing reads.
2. Deploy dual writes and a resumable batch backfill command.
3. Validate that every active, eligible memory has tokens or is intentionally tokenless.
4. Enable indexed reads with a feature flag and record candidate counts/latency.
5. Remove the full-scan compatibility path in a later cleanup after rollback confidence.

Index creation should use PostgreSQL's concurrent mode in an explicit autocommit migration path rather than inside the existing startup transaction. Backfill uses primary-key checkpoints and bounded batches. Fresh databases create the column and index directly.

Alternative considered: PostgreSQL `tsvector` alone. The project's current CJK bigram semantics are not preserved by the default text search configurations. `pg_trgm` alone is useful for fuzzy substrings but does not preserve word/bigram token semantics or BM25 candidate scoring.

### 7. Bound the neural-rerank payload independently

Keep the existing maximum of 40 rerank candidates, add a per-candidate character cap and a total character/token cap, and truncate deterministically after retrieval-fusion ordering. Source IDs remain intact so returned rankings still map safely. Diagnostics report original count, submitted count, and whether text was truncated; they do not contain the text.

Alternative considered: rely on provider context limits. Provider rejection happens too late, varies by model, and gives no stable latency or payload bound.

## Risks / Trade-offs

- [A short phase deadline may reduce relevance during provider slowness] -> Return explicit degradation metadata, preserve deterministic RRF evidence, and tune budgets from measured phase telemetry.
- [Partial results could be mistaken for complete deep enrichment] -> Expose `degraded` and phase outcomes to Pi and require final answers to disclose material evidence limitations.
- [Fallback duplicates semantic and keyword work] -> Trigger it only when deep produced no usable evidence and allow one attempt per invocation/turn budget.
- [Token preselection changes the corpus used for BM25 statistics] -> Use a generous bounded candidate cap, compare relevance against the current implementation on a frozen corpus, and retain a temporary feature flag for rollback.
- [GIN index creation/backfill adds database load] -> Use concurrent index creation, resumable batches, operational progress metrics, and do not switch reads until completeness validation passes.
- [Structured logging increases volume] -> Log only phase transitions and terminal summaries, sample successful requests if needed, and always retain timeout/cancellation summaries.
- [Cancellation of a third-party HTTP request may not stop remote computation] -> Close the local HTTP request immediately and document that cancellation guarantees local resource cleanup, not remote provider execution.

## Migration Plan

1. Add settings, internal phase outcomes, telemetry, deadline tests, and Pi hierarchy validation while leaving indexed keyword reads disabled.
2. Add the token column, dual writes, concurrent index creation command, resumable backfill, and completeness check.
3. Backfill a staging copy, compare old/new relevance and benchmark at 2,817 and at least 30,000 synthetic memories; then enable indexed reads in staging.
4. Deploy engine changes first with 45-second deep total and indexed reads gated off. Confirm degraded searches and cancellation produce correlated diagnostics.
5. Complete production backfill and enable indexed keyword reads. Monitor phase percentiles, degradation rate, fallback rate, result counts, and database load.
6. Deploy Pi with 60-second deep tool, 180-second turn, and 60-second reserve. Verify one fallback and sufficient answer time through the real SDK/SSE path.
7. Roll back by disabling indexed reads, restoring previous Pi timeout values that still satisfy the new validation, and reverting engine routing. The additive database column/index and response fields can remain safely in place; no stored memories are deleted.
