## Context

See `proposal.md` for motivation and the delta specs for behavior. Today Pi runs conversation-only fast recall in `before_agent_start` and appends up to five memories / 6,000 characters to the system prompt before the model chooses a knowledge tool. Ordinary Hindsight recall has no default source filter, so file chunks and multiple memory representations compete again in semantic, keyword, graph, and temporal arms. Conversation-only recall has a lower semantic floor (0.25 versus 0.45).

Keyword retrieval scores file chunks and memory units as separate BM25 corpora, merges their uncalibrated raw scores, and then gives the merged order one RRF arm. File keyword search uses substring predicates; memory indexed preselection exists but remains feature-gated off by default. Final MMR selection has no source, document, or turn quota.

Metadata-prefixed retrieval views and a re-embedding backfill now make files more discoverable, but repeat the same metadata in every chunk vector. Deep recall has a 45-second monotonic budget and phase timeouts, yet eagerly runs query analysis, all eligible retrieval arms, and LLM reranking; optional slow work therefore remains on the critical path. The existing database remains authoritative, raw document text and visible Pi transcripts must remain intact, and deployment remains pipeline-managed.

## Goals / Non-Goals

**Goals:**

- Make source authority explicit before retrieval and prevent conversation context from displacing document evidence.
- Stop the document-answer-to-conversation-memory feedback loop while preserving useful durable user context.
- Give lexical, semantic, and rerank scores comparable meanings through source-local ranking and hierarchical document selection.
- Improve document recall when local chunks lack document-level topic words without contaminating all chunk vectors with repeated metadata.
- Bound online work and make deep-search degradation predictable at current and projected scale.
- Provide reversible data migrations and a release gate that measures quality, leakage, latency, and provenance.

**Non-Goals:**

- Removing visible conversation transcripts or eliminating explicit conversation-memory tools.
- Treating conversation context as a substitute for indexed organizational knowledge.
- Adding a new vector database, search service, or mandatory external dependency.
- Training a learned ranker in this change; weights are configuration plus benchmark-calibrated defaults.
- Rewriting extraction/OCR quality, graph storage, or the document versioning model.
- Automatically executing cleanup or production rollout as part of implementation tests.

## Decisions

### 1. Route before recall, with knowledge as the conservative default

Introduce a deterministic-first route classifier returning `knowledge`, `continuity`, or `mixed`, plus confidence and reasons. Explicit phrases about prior conversations/preferences/decisions select continuity; explicit document/file/policy/search requests select knowledge. A bounded model classifier may resolve only the remaining ambiguous cases. Low confidence resolves to document-first knowledge retrieval with only the configured bounded auxiliary-memory lookup.

The classifier receives the current prompt and a small recent-visible-context window, never retrieved memory. Its result is recorded in turn diagnostics without raw content. Pi starts its turn deadline before classification so routing cannot create an unbounded pre-tool phase.

Alternative: keep automatic recall and rely on stronger prompting. Rejected because system-level historical content still arrives before evidence and prompt compliance is not a source-isolation control. Alternative: disable conversation memory globally. Useful as a P0 kill switch, but it removes legitimate continuity behavior rather than routing it.

### 2. Default knowledge tools to uploads; make mixed retrieval explicit

The public knowledge-search wrappers independently query `source_types=("upload",)` for the full requested quota and, when enabled, `source_types=("conversation",)` for a small auxiliary cap. Conversation recall keeps its private conversation-only contract. A new additive route/source-group request field permits explicit mixed retrieval. Existing `sources` remains populated with document evidence for compatibility; additive `document_evidence`, `conversation_context`, `route`, and `authority` fields expose separation. The two pools are never fused by comparing raw BM25 scores.

The agent system prompt states that knowledge claims require document evidence and citations, while conversation context may personalize but cannot establish a document claim. Citation extraction ignores conversation-only sources.

Alternative: allow mixed search and apply a conversation penalty. Rejected because a penalty can still leak memories when documents are sparse and cannot express source authority.

### 3. Retain durable assertions, not completed turns verbatim

Keep the reliable queue and idempotent turn identity, but add a retention policy before extraction/publication. Eligible user content is limited to durable preferences, facts, decisions, commitments, and stable state. Assistant text is excluded by default when it is a summary, tool output, document quotation, generic advice, acknowledgment, or unsupported assertion.

An assistant-originated record is eligible only when the turn contains explicit user confirmation or it represents an agent commitment/state transition allowed by policy. It stores `origin`, `confirmed_by_turn_id`, and `derived_from` evidence IDs. Tool activity captured by the runtime supplies document provenance out of band; it is never inferred from assistant prose.

Canonical content fingerprints plus semantic duplicate matching collapse repeats. Current/superseded/expired state is explicit; retrieval applies freshness and source-quality factors after relevance, and ordinary recall excludes non-current records. Audit revisions remain accessible through administrative paths.

Alternative: summarize every turn more aggressively. Rejected because compression does not fix authority laundering or duplicate growth. Alternative: retain only user text. Safer, but loses confirmed shared decisions and legitimate assistant commitments; policy-gated derived records preserve those cases.

### 4. Separate candidate pools and fuse calibrated ranks, not raw BM25

Create independent upload and conversation retrieval plans. BM25 statistics and candidate ordering stay local to a homogeneous field/source pool. Within each pool, lexical, dense, graph, and temporal ranks are converted to reciprocal ranks or normalized percentiles. Query-route weights combine those ranks; raw BM25 values never cross source boundaries.

For documents, use BM25F-style field contributions with configurable title, filename, overview, tag/entity, and body weights. For conversations, add freshness, confirmation/origin quality, and supersession state. Before reranking, collapse candidates by source identity and derivation; enforce per-document and per-turn caps. Final selection applies route quotas first and diversity second.

Initial weights and limits are configuration, not contract. Defaults are accepted only after frozen-corpus ablation against the current RRF implementation.

Alternative: lower BM25 `b` for conversations. This reduces short-text advantage but leaves incomparable IDF populations and candidate crowding. Alternative: min-max normalize raw scores per request. Rejected as unstable with small candidate sets; ranks/percentiles are more robust.

### 5. Add a document parent index and restore local chunk identity

Persist one current document retrieval record per document revision. It contains independent fields for title, filename, bounded overview, tags, and entities, with a document embedding created from those fields. The existing chunk embedding returns to original chunk text for new writes. Hierarchical recall first selects bounded document candidates, then searches original chunks only inside those documents and returns at most a configured number per document.

During migration, dual-read comparison observes old metadata-prefixed chunk retrieval and new hierarchical retrieval. The switch occurs per scope only after every current indexed document has a parent record and original-text chunk vectors. The old retrieval view fields remain until rollback confidence is established.

If metadata finds a document but no chunk clears passage relevance, return either the best bounded original passage with a low-passage-confidence marker or metadata-only document evidence with no fabricated excerpt; the answer must disclose the limitation.

Alternative: continue prefixing every chunk. Rejected because it distorts local semantics, duplicates field frequency, and clusters same-document chunks. Alternative: retrieve only document summaries. Rejected because answers require original evidence passages.

### 6. Make deep search evidence-driven and optional-phase aware

Phase zero performs bounded document lexical/dense retrieval and within-document passage selection concurrently. A deterministic sufficiency gate considers top-score margin, term coverage, document coverage, and query features. It returns early for sufficient simple evidence.

Only queries with temporal cues start temporal search; only entity/relation or cross-document cues start query analysis and graph expansion. Neural reranking runs only when multiple plausible candidates remain or the query requests comparison/synthesis. Optional failures preserve deterministic calibrated ranking. The total monotonic deadline remains authoritative and each phase receives the lesser of its configured limit and remaining time.

The one Pi fallback uses a document-only indexed lexical/dense path with no query-analysis or rerank LLM. It shares cancellation but has its own reserved slice inside the turn budget. Successful empty, degraded, timed-out, cancelled, and unavailable outcomes remain distinct.

Alternative: merely increase deadlines. Rejected because cost and tail latency grow with corpus size. Alternative: always run all arms in parallel. It minimizes ideal-case latency but forces every request to wait for slow optional work or its timeout.

### 7. Treat rollout state and cleanup as durable operations

Add scope-level retrieval migration state and per-document checkpoints. Rollout stages are `prepared`, `dual_write`, `backfilled`, `validated`, `read_enabled`, and `verified`, plus failed/retry metadata. Network embedding work happens outside long transactions; commit rechecks document revision and scope lease.

Conversation cleanup produces a manifest classifying active records as keep, duplicate, superseded, expired, disallowed-assistant-derived, or unknown. Unknown provenance blocks automatic retirement. Dry-run exports IDs, hashes, counts, and derivation edges without unnecessary text. Execution retires records from ordinary recall and invalidates cache/graph/mental-model dependents; it does not delete transcripts or uploaded documents.

Alternative: one deploy-time migration. Rejected because embeddings and cleanup are expensive, provider-dependent, and not safely transactional across the whole corpus.

### 8. Validate behavior at unit, contract, integration, replay, and scale levels

Extend the frozen benchmark with adversarial short-conversation/long-document pairs, metadata-only topics, duplicate representations, preference supersession, and assistant answer laundering. Store route, source mix, document recall, passage relevance, precision/nDCG, unsupported-answer rate, provenance correctness, per-phase percentiles, fallback outcomes, candidate counts, and payload size.

Mandatory release gates are: zero conversation leakage into knowledge-route document evidence, compliance with the auxiliary-memory cap, all required metadata-only cases found, no disallowed assistant summaries retained, citation IDs correspond to returned documents, payload/deadline contracts pass, and no statistically material regression beyond versioned tolerances. Run current-size and >=30,000-record scale suites before enabling new reads.

## Risks / Trade-offs

- [Conservative routing misses a useful memory] -> Mixed/continuity remains explicit, low confidence defaults safely, and route false negatives are measured separately from leakage.
- [Document-only defaults change clients that expected conversations in `sources`] -> Preserve explicit source filters and add mixed routing; document-only behavior applies only when the caller omitted source intent and is documented as the intentional semantic change.
- [Selective retention drops a useful assistant commitment] -> Support policy-gated confirmed commitments with provenance and retain rejected decisions in visible transcripts.
- [BM25F weights overfit the benchmark] -> Use multilingual held-out cases, publish ablations, and keep weights configurable with versioned defaults.
- [Document-first gating loses a uniquely relevant chunk] -> Use a hybrid safety lane for a small number of globally strong chunk candidates and measure its incremental recall versus noise.
- [Dual embeddings temporarily increase storage and embedding cost] -> Batch by scope/document, persist checkpoints, enforce token/cost limits, and remove old vectors only after rollback confidence.
- [Cleanup misclassifies old memories] -> Unknown provenance blocks retirement; use dry-run manifests, protected-source checks, and forward-only retirement rather than physical deletion.
- [Adaptive deep search returns too early] -> Calibrate sufficiency on complex-query false positives and always expose chosen/skipped phases in trace.
- [Indexed fallback shares a failed database] -> Distinguish database unavailability from optional-phase failure; fail quickly and preserve a truthful error instead of retry loops.

## Migration Plan

1. Add route/source contracts, tracing, kill switches, and knowledge-tool upload defaults. Deploy with automatic conversation injection disabled by default while explicit conversation tools remain available.
2. Add selective retention policy, origin/derivation/freshness fields, duplicate/supersession behavior, and tests. Begin dual writing new conversation records without cleaning history.
3. Add document parent retrieval records, original-text chunk vectors, fielded lexical state, migration checkpoints, and dual-write paths. Do not switch reads.
4. Run dry-run backfill in staging and an isolated production-data copy. Verify document revision fencing, lexical completeness, vector dimensions/model identity, and projected cost.
5. Run current and >=30,000-record offline suites comparing current, source-separated, and hierarchical variants. Approve concrete weights, quotas, caps, and sufficiency thresholds from recorded reports.
6. Enable hierarchical/indexed reads in staging per scope. Replay knowledge, continuity, mixed, metadata-only, uncovered, deep-timeout, and cancellation cases through MCP and Pi/SSE.
7. Generate and review the production retrieval and conversation-cleanup manifests. Back up required state, then backfill and enable reads in bounded scope batches; monitor leakage, quality, latency, degradation, fallback, and database load.
8. Retire only manifest-approved duplicate, superseded, expired, and disallowed assistant-derived memories. Rebuild/invalidate dependent graph, cache, observation, and mental-model state, then run protected-content checks.
9. Roll back reads by disabling the scope switch and restoring old retrieval routing. Stop cleanup and retain already retired records as inactive audit history; reactivate only from a verified manifest when no conflicting newer state exists. Application release/rollback continues through the existing pipeline.

## Open Questions

- Exact field weights, route-confidence thresholds, source quotas, per-document limits, time-decay curve, and evidence-sufficiency thresholds are intentionally benchmark-selected configuration values; the architecture and required gates do not depend on their numeric defaults.
