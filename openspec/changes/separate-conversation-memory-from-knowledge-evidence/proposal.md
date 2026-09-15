## Why

Knowledge-base answers currently overuse conversation-derived memories because automatic recall injects them before tool selection and ordinary search lets conversation memories compete directly with file evidence. Short conversation facts also receive structurally favorable lexical scores, while document metadata, source diversity, and deep-search work are not modeled as separate retrieval concerns, producing noisy results, weak document grounding, and avoidable timeouts as memory volume grows.

## What Changes

- Separate indexed-file evidence from conversation context at the query boundary. Knowledge searches preserve the full current-upload quota and may independently retrieve a small, configurable auxiliary-memory set; conversation memory cannot silently substitute for document evidence or citations.
- Replace unconditional pre-response memory injection with conservative intent gating, smaller budgets, explicit provenance, and fail-open behavior. Mixed queries expose document evidence and conversation context as separate groups with document evidence authoritative for knowledge-base claims.
- Restrict durable conversation retention to user-originated durable facts, preferences, decisions, commitments, and state. Do not retain ordinary assistant summaries, tool results, document excerpts, or unsupported model output as authoritative memory; preserve derivation provenance where an allowed assistant-originated record depends on documents.
- Retrieve and rank uploads and conversations in independent candidate pools. Do not compare raw BM25 scores across heterogeneous corpora; use source-local ranking/calibration, query-dependent source weights, source quotas, per-document/per-turn caps, and duplicate collapse before final selection.
- Introduce hierarchical document retrieval: search a document-level metadata representation first, then retrieve original chunks inside the selected documents. Keep title, filename, overview, tags, and entities independently weighted instead of repeating metadata as the sole signal in every chunk.
- Make deep search adaptive: start with bounded lexical/vector evidence, invoke query analysis, graph, temporal, and neural reranking only when needed, allow early completion when evidence is sufficient, and preserve one bounded non-LLM fallback with phase-level diagnostics.
- Add migration and cleanup procedures for document retrieval views, lexical indexes, duplicated conversation memories, and assistant-derived historical memories, with dry-run, resumability, scope fencing, audit counts, and rollback protection.
- Establish offline and live validation covering source isolation, ranking quality, metadata-only discovery, memory precision, provenance, latency, fallback, payload size, and regressions at current and projected corpus sizes.

## Capabilities

### New Capabilities

- `retrieval-source-routing`: Query intent classification, independent document/conversation retrieval channels, source authority, quotas, and mixed-query result semantics.
- `hierarchical-document-retrieval`: Document-level metadata retrieval followed by bounded within-document chunk selection and source-aware ranking.

### Modified Capabilities

- `conversation-memory`: Replace unconditional recall and broad whole-turn retention with intent-gated recall, selective retention, derivation provenance, deduplication, expiry, and supersession behavior.
- `search-evidence`: Make ordinary knowledge search document-first with bounded auxiliary conversation context, separate heterogeneous lexical scoring, collapse duplicates, and require document-grounded citations.
- `knowledge-tools`: Expose source-grouped evidence and adaptive deep-search degradation without breaking existing required fields.
- `retrieval-benchmark`: Add source-isolation, hierarchical-recall, memory-contamination, scale, latency, and fallback acceptance suites.
- `app-deployment`: Require safe rollout of lexical indexes and retrieval migrations, and align the memory, engine, tool, and turn deadline hierarchy.

## Impact

- **Engine:** `hindsight_components/recall.py`, `repository.py`, `file_chunk_recall.py`, retention/consolidation code, retrieval-view/index models and migrations, query contracts, telemetry, and cleanup/backfill runners.
- **Agent:** Pi conversation-memory extension, system/tool routing prompts, TKB search wrappers, response grouping, citation handling, deadline/fallback logic, and configuration defaults.
- **Storage:** document-level retrieval representation/index, source and derivation metadata, lexical-index rollout state, and migration audit records; existing raw documents and visible transcripts remain intact.
- **APIs:** existing search fields remain available, while grouped document evidence, bounded conversation context, authority/provenance, and richer trace fields are additive. Default knowledge-search source selection changes from one mixed competition to independent document-primary and auxiliary-memory pools.
- **Operations:** requires staged backfill and cleanup with preflight counts, dry-run review, bounded batches, rollback artifacts, and post-deploy replay/latency monitoring.
