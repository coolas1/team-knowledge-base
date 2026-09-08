# Deep search deadlines and lexical-index rollout

Deep recall uses a 45-second service deadline by default. Query analysis,
embedding, each retrieval arm, and neural reranking are capped at 8, 10, 5,
and 12 seconds respectively. Pi allows 60 seconds for the MCP tool inside a
180-second turn and preserves the final 60 seconds for fallback and answer
synthesis. Keep this ordering when tuning values:

```text
HINDSIGHT_DEEP_TOTAL_TIMEOUT_SECONDS
  < TKB_DEEP_TOOL_TIMEOUT_MS / 1000
  < PI_AGENT_MAX_RUN_SECONDS - PI_AGENT_TURN_RESERVE_SECONDS
```

## Lexical index deployment

Use an expand/backfill/switch rollout. PostgreSQL remains authoritative and no
memory rows are deleted by this process.

1. Deploy the new webapp with `HINDSIGHT_KEYWORD_INDEX_ENABLED=false`. Startup
   adds the nullable `memory_units.lexical_tokens` column and creates its GIN
   index concurrently. New and replaced memories are dual-written with tokens.
2. Inspect the pending count without writing:

   ```powershell
   docker compose exec webapp uv run python -m src.engine.hindsight_components.lexical_backfill --dry-run
   ```

3. Run the resumable backfill. Re-running this command is safe; committed
   batches are skipped and concurrently created memories already contain
   tokens.

   ```powershell
   docker compose exec webapp uv run python -m src.engine.hindsight_components.lexical_backfill --batch-size 500
   ```

4. Require `complete=true` and `remaining=0`, then set
   `HINDSIGHT_KEYWORD_INDEX_ENABLED=true` and recreate the webapp.
5. Compare indexed and compatibility-path results on the frozen release query
   set. The release target is at least 95% recall@10 against the compatibility
   path, p95 keyword retrieval below two seconds at 30,000 active memories, and
   no request materializing more than `HINDSIGHT_KEYWORD_CANDIDATE_LIMIT`
   records for Python BM25.

To roll back reads, set `HINDSIGHT_KEYWORD_INDEX_ENABLED=false` and recreate
the webapp. Keep the column, dual writes, and index in place; they are backward
compatible and make switching forward safe. The backfill command never removes
stored memories.

## Diagnostics

Hindsight emits `hindsight.deep_search.phase.start`,
`hindsight.deep_search.phase.complete`, and
`hindsight.deep_search.complete` records. Filter by `search_id` to correlate
the request with Pi's deep-search and fallback activity. Phase records include
only phase name, outcome, elapsed time, bounded component name, and sanitized
failure category. Query text, evidence, model payloads, credentials, document
titles, and document identifiers are omitted.

Track counts and latency grouped by `search_outcome` and phase outcome:

- deep-search p50, p95, and p99 duration;
- `timed_out` and `failed` phase rates;
- degraded-result and Pi fast-fallback rates;
- candidate, selected, and rerank truncation counts;
- cancellations, which must remain separate from timeouts.

For a timeout, find the Pi event's `searchId`, locate the matching Hindsight
terminal log, and inspect the last phase whose outcome is `timed_out` or
`failed`. Repeated query-analysis or rerank timeouts point to the configured LLM
endpoint. Repeated embedding timeouts point to the embedding service. Keyword
latency with indexed reads enabled should be checked together with PostgreSQL's
GIN-index usage and the configured candidate cap.
