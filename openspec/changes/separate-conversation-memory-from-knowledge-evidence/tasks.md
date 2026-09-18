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
- [x] 7.6 Extend startup validation to include routing, automatic memory recall, deep tool, fallback, PPT exception policy, final-answer reserve, and outer turn deadline; verify invalid compositions fail with actionable messages.
- [x] 7.7 Enable indexed keyword reads only after completeness validation and remove full-corpus Python BM25 from the enabled path; verify SQL plans/candidate counts remain bounded at projected scale and the feature-off rollback path still works.

## 8. Backfill and historical cleanup tooling

- [x] 8.1 Add durable scope/document migration states, revision/generation fences, leases, retries, token/cost limits, and resumable checkpoints; verify interruption and concurrent-edit integration tests.
- [x] 8.2 Implement a dry-run document migration manifest that reports current revisions, missing parent records, chunk vector/model state, lexical completeness, estimated embedding work, and protected counts; verify dry-run performs no writes.
- [x] 8.3 Implement bounded document-parent creation and original-chunk re-embedding outside long transactions with revision recheck at commit; verify restart resumes without duplicate records and skips changed revisions.
- [x] 8.4 Implement a conversation cleanup classifier and manifest for keep, duplicate, superseded, expired, disallowed-assistant-derived, and unknown records; verify unknown provenance blocks automatic retirement.
- [x] 8.5 Export rollback-safe IDs, hashes, states, derivation edges, and counts without unnecessary source text or credentials; verify export integrity and restoration preconditions.
- [x] 8.6 Execute authorized cleanup as forward-only retirement from ordinary recall and invalidate dependent cache/graph/observation/mental-model state; verify transcripts, uploaded documents, unrelated scopes, and retained audit history remain unchanged.
- [x] 8.7 Add post-migration verification and rollback commands for completeness, active-target absence, protected checksums, index state, vector model identity, dependency queues, and read-switch status; verify them against an isolated database rehearsal.

## 9. Retrieval quality and regression suite

- [x] 9.1 Add adversarial benchmark cases where short conversations share one or more terms with long relevant documents; verify knowledge-route conversation leakage into document evidence is exactly zero, auxiliary context is capped, and document recall is scored.
- [x] 9.2 Add continuity and mixed-query cases with relevant/irrelevant, fresh/stale, confirmed/unconfirmed, and superseded memories; verify route accuracy and conversation precision@3.
- [x] 9.3 Add metadata-only and implicit-topic document cases across title, filename, overview, tags, entities, noisy OCR, and multilingual content; verify document Recall@5 and passage usefulness separately.
- [x] 9.4 Add retention-contamination replays covering repeated document answers, tool payloads, assistant hallucinations, changed preferences, duplicate turns, confirmed plans, and expiry; verify authoritative active-memory counts and provenance.
- [x] 9.5 Add no-answer and weak-overlap cases; verify false positives, unsupported-answer rate, and fabricated citations do not exceed versioned tolerances.
- [x] 9.6 Run ranking ablations for current retrieval, source isolation only, source-local fusion, hierarchical retrieval, and safety lane; record MRR/nDCG/Recall@k, unique-document coverage, latency, and selected configuration.
- [x] 9.7 Build a >=30,000-record scale fixture with realistic source and length distributions; verify indexed candidate bounds, database plans, memory use, p50/p95/p99 per phase, deep outcome rates, and response budgets.
- [x] 9.8 Add deterministic failure injection for routing, embedding, lexical DB, graph, temporal, reranker, evidence load, MCP, and cancellation; verify truthful empty/degraded/timeout/unavailable/fallback outcomes and no task leaks.

## 10. Integrated acceptance and rollout evidence

- [x] 10.1 Run `uv run ruff check`, `uv run pytest`, explicit `uv run pytest src/engine/hindsight_components/tests`, and Pi `npm test`; verify all applicable unit, contract, and component suites pass without modifying unrelated user files.
- [x] 10.2 Run isolated PostgreSQL/pgvector and Neo4j integration tests for source filters, indexes, hierarchy, revision fencing, cleanup invalidation, cancellation, and rollback; verify resources are cleaned up and reports persist.
- [x] 10.3 Replay the prior automatic-driving incident and representative knowledge/continuity/mixed/no-answer conversations through MCP and Pi/SSE; verify both target documents surface, irrelevant history does not, citations are correct, and turns settle within budgets.
- [x] 10.4 Validate every search payload remains within configured whole-response bounds after adding grouping and trace fields; verify trimming removes lowest-ranked optional content first and remains self-describing.
- [x] 10.5 Rehearse schema preparation, dual write, dry-run, backfill, validation, read switch, cleanup, dependency rebuild, rollback, and resume on an isolated production-shaped copy; verify stage counts and protected checksums at every transition.
- [x] 10.6 Publish a release acceptance report containing build/SHA, configuration and policy versions, corpus hashes, quality metrics, source leakage, provenance checks, latency percentiles, phase/fallback outcomes, migration counts, exceptions, and rollback evidence; verify all mandatory gates pass before enabling production reads.
- [ ] 10.7 Roll out through the pipeline in bounded scopes, monitor search quality and resource metrics after each batch, and stop promotion on any gate failure; verify final production state has complete indexed/hierarchical reads, zero conversation leakage into document evidence, bounded auxiliary memory, no disallowed active memories, and recoverable audit artifacts.
- [x] 10.8 Preserve valid sibling facts when one model-produced fact is malformed, persist and log sanitized extraction error codes, and make document-operation retry schedule actual reingestion; verify partial extraction, secret-safe diagnostics, and document/conversation retry routing.
- [x] 10.9 Close browser-regression gaps by recovering abandoned consolidation leases, exposing a guarded conversation-cleanup CLI, rendering Markdown math, sanitizing legacy incomplete chat projections, and improving dense graph layout; verify unit suites, production frontend build, and read-only browser checks.

## 11. Post-merge review follow-ups

独立复查（PR #23 合并后）发现的缺陷与修复。10.x 已完成项的回归修复记在下面，
已修复的条目在修复 PR 中带测试。

### 11.1 已修复

- [x] 11.1a 深检索的"按设计跳过"不再算降级：`document_index_fallback` 跳过
  归类为 `primary_evidence_available`，但 `_degraded_phases` 白名单只认
  `fast_mode`/`not_requested`，导致每次成功的深检索都被标记 `degraded`；
  同时补齐 `adaptive_simple_query`/`not_required`/`not_supported`/
  `deterministic_evidence_sufficient`。
- [x] 11.1b 宽容解析后重映射 `caused_by`：兄弟事实被拒后按原索引取值会
  把因果链指向错误事实或误判为自指而丢弃；改为映射到幸存位置，指向被拒
  事实的链接直接丢弃。
- [x] 11.1c 提取缓存键升到 `tkb-extraction-v5`：`lifecycle_key`/`expires_at`
  进入 schema 后未升版，旧缓存载荷会让新字段恒为 None，覆盖与过期静默失效。
- [x] 11.1d 部分解析结果用新的 `partial` 结果码：此前与提供方异常共用
  `degraded`，会让确定性失败烧完 10 次重试阶梯并终态 `failed`；现在
  `degraded` 仍可重试，`partial` 一次完成并记日志。
- [x] 11.1e 取消也要收尾：`CancelledError` 是 `BaseException`，不经过
  `except Exception`，被取消的 pipeline 会把检索父行永久留在 `pending`
  （读侧只认 `ready`）。pipeline 取消时 shield 一次失败收尾并原样抛出；
  新增启动对账 `reconcile_interrupted_processing` 清理上一进程残留的
  `processing` 文档与 `pending` 父行。注意后端"取消 ≠ 失败"的既有约定
  （`test_cancelled_background_task_is_not_reported_as_failure`）保持不变。
- [x] 11.1f 关键词臂按池独立截断：记忆池是 overlap/BM25 计数、文件池是
  字段加权分，合并后共用 `[:limit]` 会让分数高的池把另一个池整体挤出，
  池内 RRF 看不到它。
- [x] 11.1g 检索细化开关的默认值歧义：`hindsight_knowledge_memory_context_enabled`
  默认 `True`，但同段注释只说"关闭态保持旧路径"，读起来像是全组默认关。
  该默认值有测试锁定（`test_infra_settings_postgres_dsn` ），且集成部署依赖它，
  因此保留 `True`，改为在注释、`.env.example` 说明与 `docs/config-reference.md`
  中写明它是唯一的默认开例外（仍受条数上限约束、不入文档证据）。
- [x] 11.1h 补齐 compose/`.env.example`/`config-reference` 漏掉的 7 个
  `HINDSIGHT_*`（每文档片段上限、每轮记忆上限、五个字段化词法权重），
  并把 `max_passages_per_document`/`max_memories_per_turn` 接进
  `build_query_service`（此前 Webapp/CLI 永远用类默认值，环境变量无效）。
- [x] 11.1i Pi 确认正则支持中文主语：边界类不含汉字，`我同意`/`我确认`
  匹配不上；改为允许紧跟在非否定汉字之后，`不同意`/`没同意`/`不确定`
  仍然拒绝。

### 11.2 待处理（需要产品/方案决策）

- [ ] 11.2a 行内 LaTeX 实际不渲染：`normalizeLatexDelimiters` 把 `\(...\)`
  改写成 `$...$`，而 `MarkdownContent` 设了 `singleDollarTextMath: false`，
  行内公式永远只是字面量（渲染验证：`\[...\]` 正常，`\(...\)` 与 `$...$`
  都不出 KaTeX）。开启单美元行内公式会让金额被当成公式
  （验证：`costs $5 and $10 total` 渲染出 2 个 KaTeX 节点），对含价格的
  知识库不可接受；正解需要转义字面 `$`（仅数字相邻，或全部非展示型 `$`），
  属产品取舍。
- [ ] 11.2b 每次召回都跑过期清理写事务：`recall.py` 在每次召回开头执行
  `expire_due_memories()`（行锁 UPDATE + 依赖失效），未受开关约束，与留存
  写方争用读路径。按 `HINDSIGHT_SELECTIVE_RETENTION_ENABLED` 关闭会连带
  停掉过期（`expires_at` 是无条件写入的），属行为回归；可选方案：改由后台
  worker 承担、限流、或明确接受该语义。
- [ ] 11.2c 检索视图回填的模型标签失真：`retrieval_view_backfill.py` 硬编码
  `embedding_model="backfill-runtime-model"`，且 upsert 的 `set_` 不含该列，
  `inspect()`/`dry_run_manifest` 的 `embedding_model == target` 统计因此失真。
  修法简单（把 `embedder._model` 透传进来并写进 upsert），但属尚无生产入口的
  迁移机制一簇，一并处理更合适。
- [ ] 11.2d 迁移安全项（与 11.2c 同簇）：`Chunk.embedding` 原地覆盖且无
  `embedding_model` 读过滤，`rollback_reads` 只切开关不还原数据；启用闸门是
  按运行（run）校验而开关是按库（bank）翻转；`rollback_reads` 无代际校验；
  每次 `init_db` 全表 UPDATE + 重建约束与非并发索引；清理回滚契约
  `validate_restoration_preconditions` 无生产调用方。
- [ ] 11.2e 验收证据未覆盖合并点：四份 acceptance JSON 来自四个不同的
  历史 SHA，`acceptance.md` 又是第三组数字；质量分块是 fixture 冒烟数据
  （README 自述"非生产质量结论"）。`deep-search-resilience` 与
  `retrieval-relevance` 两个主规格被本次改动实质修改却没有 delta。
