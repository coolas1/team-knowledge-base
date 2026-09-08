## 1. Plugin loader and skill folder convention

- [x] 1.1 Implement the plugin folder convention (`plugin.yaml` manifest, `skills/<name>/{skill.yaml, skill.py}`, manifest skill list with folder-scan fallback) and the loader that turns it into a loaded plugin; verify `tests/agent/test_loader.py` and `tests/agent/test_skills_folder.py` pass.
- [x] 1.2 Port the three skills (`search_and_answer`, `ingest_and_summarize`, `reflective_search`) from engine-client calls to direct in-process `KnowledgeBase` contract calls, each with its `skill.yaml` manifest; verify skill tests cover the in-process path with no HTTP transport.

## 2. Policy-as-data hooks

- [x] 2.1 Implement the hook YAML format (op, requires_approval, question) and the policy gate that evaluates an operation to a serializable proceed / needs-approval result; verify `tests/agent/test_policy.py` covers gated-unapproved, gated-approved, and ungated operations.
- [x] 2.2 Ship the `before_remove` (approval required) and `after_indexed` (no approval) hook declarations in the tkb plugin; verify the loaded plugin exposes both hook decisions.

## 3. MCP server moves into the plugin

- [x] 3.1 Rewrite the MCP server as part of the plugin (FastMCP under `src/agent/tkb/mcp/`), delete `src/engine/mcp.py`, and wire engine/query/hooks/conversation-memory services via in-process setters; verify `tests/agent/test_mcp.py` covers the tool surface.
- [x] 3.2 Guard `remove_document` with the remove hook (unapproved → needs-approval result, approved → delete) and register memory tools only when a query service is wired, with `search` falling back to plain recall; verify MCP tests cover both tool-list states.
- [x] 3.3 Mount the plugin MCP app at `/mcp` in the webapp and make BFF startup the single wiring path (init_db → build_engine → optional query service → plugin load → build_llm → MCP set_* → optional graph worker); verify the BFF test suite boots the app through this path.

## 4. Layout and deployment

- [x] 4.1 Re-land the three-module restructure without directory renames so the layout follows `origin/main` exactly (engine/agent/frontend with `hindsight_components/`, `extensions/`, `config/` at module roots); verify `uv run pytest` passes (307 passed at land time) and the app boots natively via `uvicorn src.frontend.webapp.server.app:app`.
- [x] 4.2 Move the Ollama compose service behind `profiles: [ollama]`; verify `docker compose config` lists ollama only under its profile and a default `docker compose up -d` does not start it.
- [x] 4.3 Add `docs/architecture.md` (single-app topology, vocabulary, surfaces, config boundaries) and update root/module CLAUDE.md files to the three-module layout; verify documented surfaces (`/api`, `/mcp`, `/health`, CLI) match the implementation.

## 5. Verification

- [x] 5.1 Run `uv run ruff check` and `uv run pytest`; record the suite result (307 passed, 7 skipped) and confirm no engine-side MCP imports remain (`grep -r "engine.mcp" src/` is empty).
- [x] 5.2 Validate the change with `openspec validate single-app-refactor --strict` and confirm the landed commit `7e851d98` contains only this change's files.
