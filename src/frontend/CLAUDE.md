# tkb/ -- the app: BFF server, SPA, agent sidecar

## Module summary

The deployable app. A FastAPI backend-for-frontend (BFF) mounts API routes
under `/api/*`, mounts the MCP server at `/mcp`, and serves the built SPA; in
dev the SPA runs on Vite and proxies `/api` to the BFF. The pi-agent sidecar
provides the agent loop over MCP; the BFF proxies `/api/agent/*` to it.

- `server/` - FastAPI BFF: `app.py`, `deps.py`, and `routes_*.py`
  (agent, config, documents, graph, search, query). `deps.py:startup()` is
  the single wiring path: `init_db` -> `build_engine(engine_config_from_app)` ->
  optional memory query service -> load plugin -> `build_llm` -> MCP `set_*` ->
  optional graph worker. Startup runs `init_db` (schema creation).
- `client/` - React 19 + TypeScript + Vite SPA (`npm run dev` :5173, `npm test`).
  `client/dist/` is the production build output, served by the BFF.
- `agent/` - the pi-agent sidecar (TypeScript, :8010): connects to the app's
  `/mcp`, exposes a curated read-only tool set, and serves an HTTP/SSE chat API
  that `/api/agent/*` proxies to.

## Hard-won knowledge

<!-- Inclusion rule: add an entry only if it is non-obvious, repo-related, and
     painful to re-derive. Each entry: the decision (1-3 sentences) + why.
     Truly long-form decisions link out to a doc instead of bloating this file. -->
