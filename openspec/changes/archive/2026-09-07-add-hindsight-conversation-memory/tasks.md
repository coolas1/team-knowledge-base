## 1. Batch 1 - Conversation Source Queue

- [x] 1.1 Add the additive conversation-memory source/queue model with document FK, unique session-turn identity, processing states, lease fields, retry metadata, and indexes; verify model metadata and PostgreSQL DDL tests cover every constraint and index.
- [x] 1.2 Implement deterministic conversation document IDs plus transactional enqueue/upsert, claim, complete, fail/retry, status-count, and session-cancel repository operations; verify repository tests cover duplicate enqueue, lease recovery, bounded retries, and cancellation races.
- [x] 1.3 Create internal conversation Document rows without GraphRAG chunks and centralize exclusion of those rows from existing document list/count/detail/removal behavior; verify engine and BFF document contract tests prove conversation sources never appear as uploaded files.
- [x] 1.4 Run `uv run ruff check` and the focused engine/frontend document and conversation-repository tests, then commit only Batch 1 files/hunks as `feat(engine): add conversation memory queue`; verify `git show --stat --oneline HEAD` contains no unrelated worktree changes.

## 2. Batch 2 - Hindsight Processing and Retrieval

- [x] 2.1 Introduce a structured Hindsight retain input carrying source type, context, tags, and metadata while preserving current file-hook defaults; verify existing retain/hook tests and new conversation transcript extraction tests pass.
- [x] 2.2 Propagate conversation session/turn provenance into retained memories and recall results while leaving existing file provenance unchanged; verify type, repository, compatibility-adapter, and query serialization tests cover both source kinds.
- [x] 2.3 Add optional repository-level source filtering to semantic, keyword, graph, and temporal retrieval and thread it through recall; verify conversation-only recall cannot be displaced by higher-ranked file memories and unfiltered existing queries still return their prior results.
- [x] 2.4 Implement the leased conversation-retention worker and runtime lifecycle wiring with bounded concurrency, retry/backoff, cancellation checks, and clean shutdown; verify worker tests cover success, provider failure, retry exhaustion, restart recovery, and forget-while-processing.
- [x] 2.5 Run `uv run ruff check` and all Hindsight component tests, then commit only Batch 2 files/hunks as `feat(engine): process conversation memories`; verify the commit excludes Batch 1-unrelated and user-owned changes.

## 3. Batch 3 - Internal Engine Transport

- [x] 3.1 Add optional engine-side conversation-memory contracts and request/result types for recall, enqueue, forget, and diagnostics without changing required `KnowledgeBase` methods; verify interface and compatibility tests pass for backends without the optional capability.
- [x] 3.2 Add private MCP operations for bounded conversation recall, turn enqueue, session forget, and queue diagnostics with validation and failure mapping; verify MCP tests cover success, invalid input, disabled Hindsight, and provider/repository failures.
- [x] 3.3 Extend runtime settings for worker poll, lease, attempts, recall limits, and feature enablement, and wire startup/shutdown in both engine MCP and in-process webapp modes; verify configuration defaults, invalid-value rejection, and lifecycle tests pass.
- [x] 3.4 Run `uv run ruff check` plus engine interface, MCP, configuration, and lifecycle tests, then commit only Batch 3 files/hunks as `feat(engine): expose conversation memory api`; verify the MCP tool list remains backward compatible and no memory write tool is model-facing.

## 4. Batch 4 - Pi Agent Automatic Memory

- [x] 4.1 Extend the Pi MCP contract/client with internal conversation-memory calls while excluding them from `buildTools()` and the model-visible tool list; verify contract and tool tests enforce both availability and invisibility.
- [x] 4.2 Add validated Pi memory configuration for enablement, recall timeout/result limit, injected context budget, and retention context; verify defaults, disabled mode, and invalid limits in configuration tests.
- [x] 4.3 Implement a runtime-owned `before_agent_start` hook that recalls by the current prompt and appends a bounded, clearly delimited untrusted-memory block only to the current system prompt; verify tests cover relevance, empty results, timeout/failure fallback, prompt-injection framing, and absence from persisted/visible messages.
- [x] 4.4 After a successful prompt, extract only the newly completed visible user/assistant turn, derive a stable persisted turn ID, and enqueue it; verify tests cover idempotent retries, excluded tools/reasoning/memory blocks, cancellation, model failure, and enqueue failure without changing the successful SSE result.
- [x] 4.5 Add memory enablement and diagnostic queue state to Pi health output without exposing retained content; verify server/runtime health tests cover enabled, disabled, healthy, and degraded states.
- [x] 4.6 Run `npm test` and `npm run check` in `src/extensions/pi-agent`, then commit only Batch 4 files/hunks as `feat(agent): automate conversation memory`; verify `git show` contains no engine or unrelated worktree changes.

## 5. Batch 5 - Forget API and Deployment Configuration

- [x] 5.1 Add a Pi session-memory DELETE route and runtime method that invoke explicit engine forgetting without changing ordinary session deletion; verify server tests prove the two deletion operations have separate effects.
- [x] 5.2 Add the BFF proxy and frontend API client method for explicit session-memory forgetting while preserving all existing session request and SSE shapes; verify BFF and client contract tests pass unchanged plus new forget cases.
- [x] 5.3 Update Compose and example configuration to start the retention worker and enable automatic memory only after the required engine MCP contract is present; preserve the pre-existing local `docker-compose.yml` GPU hunk and verify `docker compose config` succeeds.
- [x] 5.4 Document shared-team scope, eventual consistency, configuration, diagnostics, normal session deletion, and explicit forgetting; verify documented endpoint names and environment variables match the implemented contracts.
- [x] 5.5 Run focused BFF, frontend client, Pi server, and configuration tests, then stage only Batch 5-owned hunks and commit as `feat(webapp): add conversation memory controls`; verify the user's unrelated `docker-compose.yml` change remains unstaged unless explicitly included by the user.

## 6. Batch 6 - End-to-End Verification

- [x] 6.1 Add an integration test that completes a conversation turn, waits for durable processing, starts or uses another session, and verifies automatic recall influences the model context without appearing in visible history.
- [x] 6.2 Add integration coverage proving duplicate enqueue is idempotent, file APIs hide internal conversation Documents, normal session deletion preserves memory, and explicit forgetting removes only the target session's conversation memories.
- [x] 6.3 Run `uv run ruff check`, `uv run pytest`, `npm test` and `npm run check` in `src/extensions/pi-agent`, and `npm test` in `src/frontend/webapp/client`; record any live-service-only cases that require `RUN_INTEGRATION=1` and run them when PostgreSQL, Neo4j, and Ollama are available.
- [x] 6.4 Run `openspec validate add-hindsight-conversation-memory --strict` and review the final diff for API compatibility, sensitive-content logging, feature-off behavior, and unrelated worktree changes.
- [x] 6.5 Commit only Batch 6 tests and verification documentation as `test(agent): verify conversation memory flow`; verify the branch history shows the planned focused commits in dependency order and the worktree retains all pre-existing unrelated changes.
