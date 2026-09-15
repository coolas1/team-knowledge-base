## 1. Baseline, contracts, and safety switches

- [x] 1.1 Capture a frozen pre-change retrieval baseline for knowledge, continuity, mixed, metadata-only, uncovered, and deep-search timeout cases; record source types, ranks, scores, citations, phase timing, payload size, and active memory counts, and verify the report is reproducible from documented commands.
- [x] 1.2 Extend the engine query contract with additive route, source-group, authority, provenance, and trace fields while preserving existing required fields; verify serialization and compatibility contract tests for legacy requests and responses.
- [x] 1.3 Add independent kill switches for automatic conversation recall, mixed-source search, hierarchical reads, adaptive deep phases, and new retention publication; verify each switch restores its documented safe fallback without disabling document search.
- [x] 1.4 Change integrated deployment defaults so automatic conversation injection is off until routing acceptance passes, while explicit conversation-memory tools remain available; verify rendered compose configuration and Pi startup behavior.
- [x] 1.5 Add sanitized metrics/log fields for route, confidence band, source pool counts, leakage, duplicate collapse, document coverage, phase outcome, fallback, and migration state; verify logs contain no query, evidence, credentials, or hidden prompts.

## 2. Query routing and source isolation

- [x] 2.1 Implement deterministic query-intent rules for explicit document/file/policy/search queries and explicit prior-conversation/preference/decision queries; verify multilingual unit cases classify knowledge and continuity correctly.
- [x] 2.2 Implement the bounded ambiguous-query classifier with confidence/reason output and a knowledge-route default on timeout, invalid output, or low confidence; verify failure-path tests never trigger automatic memory recall.
- [x] 2.3 Supply only the current prompt and a bounded recent-visible-context window to routing, start the turn deadline before routing, and exclude retrieved memory from classifier input; verify Pi extension tests and deadline accounting.
- [x] 2.4 Make `tkb_search_fast`, `tkb_search_deep`, legacy search, and default `query_knowledge` recall preserve the full uploaded/current-document quota while independently returning at most the configured auxiliary memories; verify mixed-corpus MCP tests return zero conversation items in compatibility `sources`/`document_evidence` and bounded items in `conversation_context`.
- [x] 2.5 Preserve explicit conversation-only recall and add explicit mixed-route execution as two independent searches; verify route-specific filters reach semantic, lexical, graph, and temporal repository paths.
- [x] 2.6 Return `document_evidence` and `conversation_context` separately for mixed queries while keeping compatibility `sources` document-grounded; verify response schemas, payload budgets, and legacy client tests.
- [x] 2.7 Update Pi system/tool instructions and citation extraction so knowledge claims require returned document title/doc_id and conversation context cannot become a document citation; verify agent tests for document-only, conversation-only, mixed, and no-document-evidence answers.

## 3. Conversation recall and injection controls

- [x] 3.1 Invoke automatic conversation recall only for accepted continuity/mixed routes and omit it for knowledge/uncertain routes; verify `before_agent_start` tests assert the MCP recall call count by route.
- [x] 3.2 Add separate result-count and character budgets for conversation context with safe closing delimiters and explicit session/turn/source-time labels; verify zero, oversized, malformed, and exact-boundary formatting cases.
- [x] 3.3 Apply configurable semantic, salient-term, freshness, confirmation/origin-quality, and current-state gates to conversation recall; verify irrelevant, expired, superseded, and low-quality assistant memories are filtered while a relevant user preference survives.
- [x] 3.4 Prevent automatic memory blocks from entering visible transcripts, retention input, document evidence, citations, and tool payload duplication; verify end-to-end transcript and next-turn retention tests.
- [x] 3.5 Keep conversation recall fail-open but diagnostically distinct from an empty result; verify timeout/unavailable cases continue the turn and emit sanitized failure metrics.

## 4. Selective retention and memory lifecycle

- [x] 4.1 Define a versioned retention-policy model covering eligible user facts/preferences/decisions/commitments/state and disallowed assistant summaries, tool results, document excerpts, acknowledgments, generic advice, transient discussion, and unsupported claims; verify policy parsing and invalid-policy rejection.
- [x] 4.2 Capture trusted tool-evidence identifiers and user-confirmation state out of band with the completed turn, without parsing assistant prose for provenance; verify Pi-to-MCP contract and tamper-resistant binding tests.
- [x] 4.3 Apply retention policy before extraction/publication while preserving reliable queue delivery and idempotent turn identity; verify eligible and ineligible turns reach the correct terminal state without duplicate work.
- [x] 4.4 Store memory origin, authority, policy version, confirmed-by turn, derived-from evidence IDs, expiry, and current/superseded state; verify schema migration, round-trip repository tests, and scope isolation.
- [x] 4.5 Reject authoritative publication of assistant-derived records lacking allowed type, explicit confirmation when required, or valid visible evidence provenance; verify adversarial assistant-summary and hallucination tests.
- [x] 4.6 Implement canonical fingerprints and bounded semantic duplicate detection across turns; verify repeated paraphrases yield one current representative and preserve audit revisions.
- [x] 4.7 Implement preference/state supersession and optional expiry/time decay without physically deleting audit history; verify changed-preference, expiration-boundary, and historical-admin-view tests.
- [x] 4.8 Invalidate fact cache, graph projection, observations, and mental-model dependents when a memory is superseded, expired, or retired; verify stale content cannot return through any read path.

## 5. Source-local lexical and rank fusion

- [x] 5.1 Refactor upload and conversation retrieval into independent candidate pools with explicit pool identities; verify repository tests never merge their raw BM25 rows before source-local ordering.
- [x] 5.2 Implement document fielded lexical scoring for title, filename, overview, tags/entities, and body with configurable weights and bounded indexed preselection; verify field contribution tests for English, Chinese, Japanese, identifiers, and single-term exact names.
- [x] 5.3 Convert lexical, dense, graph, and temporal results to source-local reciprocal ranks or stable percentiles and combine them with route-specific weights; verify score invariance when unrelated records are added to the other source pool.
- [x] 5.4 Add conversation freshness, confirmation/origin authority, and supersession factors after relevance gating; verify short low-quality memories cannot outrank a relevant confirmed memory solely because of text length.
- [x] 5.5 Collapse source chunk/fact/observation and repeated-turn representations by source, derivation, and semantic identity before reranking; verify the strongest provenance-bearing representative is retained and trace reports collapsed counts.
- [x] 5.6 Enforce route source quotas, per-document caps, per-turn caps, and final diversity ordering before token selection; verify large conversation and single-document floods cannot exhaust final evidence slots.
- [x] 5.7 Add configurable hybrid safety-lane candidates for globally strong original chunks outside parent-document selection; verify an ablation demonstrates added recall without violating document/source caps.

## 6. Hierarchical document retrieval

- [x] 6.1 Add a revision-aware document retrieval record containing independent title, filename, overview, tags, entities, field tokens, embedding, model identity, and generation state; verify schema creation, uniqueness, current-version fencing, and scope predicates.
- [x] 6.2 Dual-write the document record on upload, edit, reingest, retry, and summary/metadata changes; verify every successful current-document path produces one matching retrieval record and failed/stale revisions cannot publish.
- [x] 6.3 Change new chunk embeddings to use original chunk text while retaining the existing user-visible text; verify embedding inputs no longer repeat document metadata and reading/citation behavior is unchanged.
- [x] 6.4 Implement bounded document lexical/dense retrieval and field-aware fusion; verify metadata-only, noisy-OCR, filename, tag/entity, and ordinary body-topic cases select the expected parent documents.
- [x] 6.5 Search and rerank original chunks only inside selected parents, enforce the configured per-document passage cap, and expose parent and passage scores; verify implicit-topic chunks are returned under the correct document.
- [x] 6.6 Handle metadata-relevant documents with no reliable passage by returning an explicit metadata-only or low-passage-confidence result without fabricating text; verify answer generation discloses the limitation.
- [x] 6.7 Add hierarchical trace fields for parent candidates, passage candidates, field contributions, safety-lane use, document coverage, caps, and collapse; verify trace is bounded and content-safe.

## 7. Adaptive deep search and deadlines

- [x] 7.1 Implement a deterministic evidence-sufficiency gate using query features, score margin, term coverage, passage confidence, and document coverage; verify simple queries stop early and complex/ambiguous fixtures escalate.
- [x] 7.2 Start temporal expansion only for temporal cues and graph/query-analysis expansion only for entity, relation, multi-hop, or cross-document needs; verify skipped/started phase traces for each query class.
- [x] 7.3 Run neural reranking only when multiple plausible candidates remain or comparison/synthesis requires it; verify timeout retains calibrated deterministic evidence and marks degradation.
- [x] 7.4 Preserve one monotonic total budget across routing, optional phases, evidence loading, and cleanup; verify every phase receives the lesser of configured and remaining time and cancellation closes descendant tasks.
- [x] 7.5 Implement a document-only indexed lexical/dense fallback with no query-analysis or rerank LLM, reserve its budget inside the turn, and allow at most one attempt; verify timeout, unavailable, degraded-without-evidence, fallback-failure, and cancellation cases.
- [ ] 7.6 Extend startup validation to include routing, automatic memory recall, deep tool, fallback, PPT exception policy, final-answer reserve, and outer turn deadline; verify invalid compositions fail with actionable messages.
- [ ] 7.7 Enable indexed keyword reads only after completeness validation and remove full-corpus Python BM25 from the enabled path; verify SQL plans/candidate counts remain bounded at projected scale and the feature-off rollback path still works.

## 8. Backfill and historical cleanup tooling

- [ ] 8.1 Add durable scope/document migration states, revision/generation fences, leases, retries, token/cost limits, and resumable checkpoints; verify interruption and concurrent-edit integration tests.
- [ ] 8.2 Implement a dry-run document migration manifest that reports current revisions, missing parent records, chunk vector/model state, lexical completeness, estimated embedding work, and protected counts; verify dry-run performs no writes.
- [ ] 8.3 Implement bounded document-parent creation and original-chunk re-embedding outside long transactions with revision recheck at commit; verify restart resumes without duplicate records and skips changed revisions.
- [ ] 8.4 Implement a conversation cleanup classifier and manifest for keep, duplicate, superseded, expired, disallowed-assistant-derived, and unknown records; verify unknown provenance blocks automatic retirement.
- [ ] 8.5 Export rollback-safe IDs, hashes, states, derivation edges, and counts without unnecessary source text or credentials; verify export integrity and restoration preconditions.
- [ ] 8.6 Execute authorized cleanup as forward-only retirement from ordinary recall and invalidate dependent cache/graph/observation/mental-model state; verify transcripts, uploaded documents, unrelated scopes, and retained audit history remain unchanged.
- [ ] 8.7 Add post-migration verification and rollback commands for completeness, active-target absence, protected checksums, index state, vector model identity, dependency queues, and read-switch status; verify them against an isolated database rehearsal.

## 9. Retrieval quality and regression suite

- [x] 9.1 Add adversarial benchmark cases where short conversations share one or more terms with long relevant documents; verify knowledge-route conversation leakage into document evidence is exactly zero, auxiliary context is capped, and document recall is scored.
- [ ] 9.2 Add continuity and mixed-query cases with relevant/irrelevant, fresh/stale, confirmed/unconfirmed, and superseded memories; verify route accuracy and conversation precision@3.
- [ ] 9.3 Add metadata-only and implicit-topic document cases across title, filename, overview, tags, entities, noisy OCR, and multilingual content; verify document Recall@5 and passage usefulness separately.
- [ ] 9.4 Add retention-contamination replays covering repeated document answers, tool payloads, assistant hallucinations, changed preferences, duplicate turns, confirmed plans, and expiry; verify authoritative active-memory counts and provenance.
- [ ] 9.5 Add no-answer and weak-overlap cases; verify false positives, unsupported-answer rate, and fabricated citations do not exceed versioned tolerances.
- [ ] 9.6 Run ranking ablations for current retrieval, source isolation only, source-local fusion, hierarchical retrieval, and safety lane; record MRR/nDCG/Recall@k, unique-document coverage, latency, and selected configuration.
- [ ] 9.7 Build a >=30,000-record scale fixture with realistic source and length distributions; verify indexed candidate bounds, database plans, memory use, p50/p95/p99 per phase, deep outcome rates, and response budgets.
- [ ] 9.8 Add deterministic failure injection for routing, embedding, lexical DB, graph, temporal, reranker, evidence load, MCP, and cancellation; verify truthful empty/degraded/timeout/unavailable/fallback outcomes and no task leaks.

## 10. Integrated acceptance and rollout evidence

- [ ] 10.1 Run `uv run ruff check`, `uv run pytest`, explicit `uv run pytest src/engine/hindsight_components/tests`, and Pi `npm test`; verify all applicable unit, contract, and component suites pass without modifying unrelated user files.
- [ ] 10.2 Run isolated PostgreSQL/pgvector and Neo4j integration tests for source filters, indexes, hierarchy, revision fencing, cleanup invalidation, cancellation, and rollback; verify resources are cleaned up and reports persist.
- [ ] 10.3 Replay the prior automatic-driving incident and representative knowledge/continuity/mixed/no-answer conversations through MCP and Pi/SSE; verify both target documents surface, irrelevant history does not, citations are correct, and turns settle within budgets.
- [x] 10.4 Validate every search payload remains within configured whole-response bounds after adding grouping and trace fields; verify trimming removes lowest-ranked optional content first and remains self-describing.
- [ ] 10.5 Rehearse schema preparation, dual write, dry-run, backfill, validation, read switch, cleanup, dependency rebuild, rollback, and resume on an isolated production-shaped copy; verify stage counts and protected checksums at every transition.
- [ ] 10.6 Publish a release acceptance report containing build/SHA, configuration and policy versions, corpus hashes, quality metrics, source leakage, provenance checks, latency percentiles, phase/fallback outcomes, migration counts, exceptions, and rollback evidence; verify all mandatory gates pass before enabling production reads.
- [ ] 10.7 Roll out through the pipeline in bounded scopes, monitor search quality and resource metrics after each batch, and stop promotion on any gate failure; verify final production state has complete indexed/hierarchical reads, zero conversation leakage into document evidence, bounded auxiliary memory, no disallowed active memories, and recoverable audit artifacts.
