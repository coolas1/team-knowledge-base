# frontend/ -- BFF server and SPA

## Module summary

The host/web layer: a FastAPI backend-for-frontend (BFF) plus a React SPA. The
BFF mounts API routes under `/api/*` and serves the built SPA; in dev the SPA
runs on Vite and proxies `/api` to the BFF.

- `webapp/server/` — FastAPI BFF: `app.py`, `deps.py`, and `routes_*.py`
  (agent, config, documents, graph, search). Startup runs `init_db` (schema creation).
- `webapp/client/` — React 19 + TypeScript + Vite SPA (`npm run dev` :5173, `npm test`).
  `client/dist/` is the production build output, served by the BFF.
- `windowsapp/` — Windows-targeted host variant (see its README).

## Hard-won knowledge

<!-- Inclusion rule: add an entry only if it is non-obvious, repo-related, and
     painful to re-derive. Each entry: the decision (1-3 sentences) + why.
     Truly long-form decisions link out to a doc instead of bloating this file. -->
