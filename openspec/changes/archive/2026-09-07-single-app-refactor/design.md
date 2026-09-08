## Context

Before this change, `src/agent/` reached the engine through a dual-transport `EngineClient` (`InProcessEngineClient` for the BFF, `McpEngineClient` over streamable HTTP for the codex harness), the MCP server lived at `src/engine/mcp.py` as a thin adapter over the `KnowledgeBase` protocol, and the codex "plugin" was a hard-coded Python module with skills as flat scripts. The repo had also attempted a three-module restructure that sat in a stash with 11 conflicts, leaving the layout diverged from `origin/main` so upstream PRs did not apply cleanly. See proposal.md - Why for the motivation.

## Goals / Non-Goals

**Goals:**
- One deployable process: BFF + engine + plugin in-process; MCP mounted by the BFF.
- The agent seam as data: plugin manifest, skill manifests, hook YAML — no agent surface inside the engine module.
- A layout that follows `origin/main` exactly, so future upstream PRs apply cleanly.

**Non-Goals:**
- No runtime plugin discovery, hot reload, or multi-plugin hosting — exactly one plugin is selected by `plugin.impl` in `config/app.yaml`.
- No new engine contracts or MCP tool semantics beyond moving the surface; tool behavior is unchanged except `remove_document` gaining the approval gate.
- No standalone MCP deployment mode; MCP is always hosted by the webapp.

## Decisions

### Folder-convention plugin instead of packaged plugins
A plugin is a directory: `plugin.yaml` (name, version, mcp endpoint, skills), `skills/<name>/{skill.yaml, skill.py}`, `hooks/*.yaml`, `mcp/`. `PluginLoader` reads the folder and imports each `skill.py` via `importlib` to extract its `run` function.
- Why: the whole agent seam is diffable, auditable YAML + small Python files; no packaging metadata or entry-point machinery. The skills are data-plus-thin-code, which is the point of "policy as data".
- Alternative: Python entry points / installed distributions — rejected: adds packaging infrastructure for a single in-tree plugin.

### Policy-as-data hooks instead of callback hooks
`HookPolicy.load(hooks/)` builds an op → question map; `check(op, params, approved)` returns `Proceed` or `NeedsApproval(question, PendingAction)` — plain serializable dataclasses, never a blocking human-in-the-loop callback.
- Why: the identical decision payload must be interpretable by an in-process loop, an MCP caller, and (later) a UI confirmation dialog. Callbacks cannot cross the MCP boundary; data can.
- Alternative: async approval callbacks — rejected: unserializable, untestable across the boundary.

### MCP server moves into the plugin; engine has no MCP
`src/engine/mcp.py` is deleted and reborn as `src/agent/tkb/mcp/server.py` (FastMCP), rewritten around module-level `set_kb` / `set_query_service` / `set_hooks` / `set_conversation_memory_service` injection. `remove_document` routes through the hook gate; memory tools register only when a query service is wired.
- Why: MCP is an agent-facing concern; the engine stays a pure knowledge-base library (CLI + contract). "Never registered-but-broken" keeps the tool list honest — an absent tool is a clearer contract than a failing one.
- Alternative: keep MCP in the engine and have the plugin wrap it — rejected: preserves the exact module-boundary smell the refactor removes, and double-wraps every tool.

### One wiring path in BFF startup
`deps.py:startup()` is the single place components are constructed: `init_db` → `build_engine(engine_config_from_app(...))` → optional query service → `PluginLoader().load(...)` → `build_llm()` → MCP `set_*` → optional graph worker.
- Why: one engine instance shared by REST, MCP, and skills; config errors surface at boot instead of at first request.
- Alternative: lazy per-route construction — rejected: risks divergent engine instances and hides config errors.

### Re-land the restructure without directory renames
The layout now follows `origin/main` exactly: `hindsight_components/` at the engine root, `frontend/`, `extensions/`, `config/` — the stashed three-module restructure minus the renames that caused the 11 conflicts.
- Why: upstream PRs (#3, #4 follow-ups) must apply cleanly; renames were the conflict source.
- Trade-off: documentation keeps upstream's conceptual vocabulary (e.g. `docs/architecture.md` says "src/tkb"), while the actual directories keep their repo-history names (`src/frontend/webapp`, `src/agent`). The module `CLAUDE.md`s are authoritative for actual paths.

### Ollama becomes opt-in via compose profile
`ollama` sits behind `profiles: [ollama]`.
- Why: with OpenAI-compatible external endpoints, a local GPU model service must not be part of the default `docker compose up -d`. (The follow-up model-config change points the default `EMBEDDING_BASE_URL` at this profile's service.)

## Risks / Trade-offs

- [Module-level MCP state (`set_*` globals)] → single app per process is the accepted deployment model; tests reset the globals explicitly.
- [In-process skills lose transport isolation — a skill exception is a BFF-process exception] → skills are stateless and thin (search/ingest/reflect wrappers over the contract); the engine already isolates per-operation failures.
- [Upstream-vocabulary docs vs. actual paths] → module CLAUDE.md files are authoritative; `docs/architecture.md` records the conceptual map.
- [One big layout commit hides the plugin-architecture diff inside path moves] → the commit message enumerates the restructures explicitly; git rename detection keeps history for moved files.

## Migration Plan

Already applied on `main` (`7e851d98`). For other deployments pulling this: MCP clients keep using `/mcp` (unchanged URL, tool set now includes the approval-gated `remove_document`); compose users who relied on the always-on Ollama service must add `--profile ollama` or point model URLs at external endpoints; any code importing the old `src.engine.mcp` or `src.agent.engine_client` must migrate to the plugin seam. Rollback = revert the commit; no data or schema changes.

## Open Questions

- Should the stale `src/plugin/` directory (compiled `__pycache__` residue from the pre-restructure layout, no source files) be deleted? Cosmetic; safe to do any time.
