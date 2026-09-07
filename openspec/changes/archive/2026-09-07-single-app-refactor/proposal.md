## Why

The agent layer reached the engine through a dual-transport `EngineClient` (in-process wrapper or MCP-over-HTTP), so every skill call either paid a network hop or routed through an adapter that duplicated contract plumbing. The MCP server lived inside the engine module (`src/engine/mcp.py`), mixing an agent-facing surface into what should be a pure knowledge-base library, and the repo layout had drifted from `origin/main`, making future upstream PRs painful to apply. The app needed to become one deployable unit — BFF + engine + plugin in a single process — with the agent seam expressed as data (manifests, hooks) rather than code.

## What Changes

- Replace the `EngineClient`-based skills with a manifest-driven plugin: `plugin.yaml` + `skills/<name>/{skill.py,skill.yaml}` + `hooks/*.yaml`, loaded by `PluginLoader` into a `LoadedPlugin`; skills call the `KnowledgeBase` contract directly, in-process, with no transport layer.
- Add a policy-as-data hook gate (`HookPolicy.check(op, params, approved)` → `Proceed | NeedsApproval`): approval policy ships as hook YAML, evaluates to a serializable result, never a blocking callback — the same payload is interpreted identically by an in-process loop or an external MCP caller.
- Move the MCP server from `src/engine/mcp.py` into the plugin (`src/agent/tkb/mcp`, FastMCP, rewritten); the webapp BFF mounts it in-process at `/mcp`.
- Make BFF startup (`deps.py:startup()`) the single wiring path: `init_db` → `build_engine` → optional query service → plugin load → `build_llm` → MCP `set_*` → optional graph worker.
- Make MCP tool registration honest: memory tools (`query_knowledge`, `search_knowledge_fast`, `search_knowledge_deep`) register only when a query service is wired (`engine.memory.enabled`); `search` falls back to plain recall otherwise. `remove_document` is hook-guarded.
- Re-land the three-module restructure without the directory renames so the layout follows `origin/main` exactly (`hindsight_components`, `frontend`, `extensions`, `config/` at module roots), keeping future upstream PRs applicable.
- Make the Ollama compose service opt-in via `--profile ollama` (external OpenAI-compatible endpoints no longer need the service).
- Add `docs/architecture.md` documenting the single-app architecture, vocabulary, surfaces, and config boundaries.

## Capabilities

### New Capabilities
- `plugin-architecture`: Manifest-driven plugin loading (skills, hooks, MCP server), policy-as-data approval gates, and the in-process MCP tool surface hosted by the BFF.

- `app-deployment`: Single-process deployment topology — one backend app (BFF + engine + plugin in-process) with a pi-agent sidecar, opt-in Ollama, and a dev environment that mirrors production.

### Modified Capabilities

None.

## Impact

- `src/agent/` — `engine_client.py`, `codex/plugin.py`, and the flat skill scripts are removed; `loader.py`, `policy.py`, and the `tkb/` plugin folder (skills, hooks, `mcp/`) are added.
- `src/engine/` — `mcp.py` removed (moved into the plugin); module-internal layout aligned with upstream.
- `src/frontend/webapp/server/` — `deps.py` startup wiring; `app.py` mounts the plugin MCP app at `/mcp`.
- `docker-compose.yml` — Ollama behind `profiles: [ollama]`.
- Docs — `docs/architecture.md` added; root and module `CLAUDE.md`s updated to the three-module layout.
- Retroactive note: this change documents work already applied to `main` as commit `7e851d98` ("refactor(agent): plugin system on upstream layout"). The artifacts record what landed; they do not gate it.
