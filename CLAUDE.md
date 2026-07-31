# team-knowledge-base

A GraphRAG-powered team knowledge base: ingest documents into a three-layer
knowledge graph (entities → relations → chunks), retrieve via semantic search
with reranking, and query through a CLI, an MCP server, or a web UI.

Three independently switchable modules live under `src/`:

- **engine** — GraphRAG storage/retrieval (Postgres+pgvector, Neo4j), CLI, MCP.
- **agent** — stateless skills + LLM orchestration (see `src/agent/CLAUDE.md`).
- **frontend** — FastAPI BFF + React SPA (see `src/frontend/CLAUDE.md`).

## Commands

Run from the repo root unless noted. Python tooling uses `uv`.

- **Install deps:** `uv sync` (add `--extra reranker` only for a local torch reranker)
- **Run tests:** `uv run pytest`
- **Integration tests:** `RUN_INTEGRATION=1 uv run pytest` (live Postgres+Neo4j+Ollama)
- **Lint:** `uv run ruff check`
- **Format:** `uv run ruff format`
- **Engine MCP server:** `uv run python -m src.engine.mcp` (port 8000, `/mcp`)
- **Engine CLI:** `uv run python -m src.engine.cli recall --query "..."`
- **BFF server:** `uv run uvicorn src.frontend.webapp.server.app:app --reload`
- **SPA (dev):** `cd src/frontend/webapp/client && npm install && npm run dev` (proxies `/api` → :8000)
- **SPA tests:** `cd src/frontend/webapp/client && npm test`

## Workflow

1. Run `uv run ruff check` and `uv run pytest` before pushing.
2. Backing services run via `docker compose up -d` (Postgres :5433, Neo4j :7687);
   copy `.env.example` to `.env` and set `OLLAMA_BASE_URL` first.
3. The reranker is configurable via `RERANKER_PROVIDER`: `http` (external
   `/v1/rerank` API, default), `local` (torch — needs `--extra reranker`), or `none`.

## Coding Standards

- **Python:** 3.12+, type-hinted. Async-first for I/O (asyncpg, SQLAlchemy async).
- **Commits:** Conventional Commits, scoped to the module touched —
  `type(scope): subject`. Types: `feat fix docs refactor test perf build chore`.
  Scopes in use: `engine agent frontend webapp reranker compose config infra`.
  Subject ≤50 chars, imperative mood, no trailing period; body wrapped at 72.
- **Frontend:** React 19 + TypeScript + Vite; colocate component tests.

## Architecture

```
src/
├── engine/        # GraphRAG engine — see src/engine/CLAUDE.md
├── agent/         # skills + LLM orchestration — see src/agent/CLAUDE.md
└── frontend/      # BFF + SPA — see src/frontend/CLAUDE.md
```

Backing services (`docker-compose.yml`): Postgres+pgvector (vectors, chunks) and
Neo4j (entity/relation graph). Ollama is external (`OLLAMA_BASE_URL`). Config
flows through `.env` → `config/settings.py` (pydantic-settings).

## Validity check

- `uv run pytest` — unit + contract + BFF tests (must pass).
- `cd src/frontend/webapp/client && npm test` — SPA api-client tests.
- `RUN_INTEGRATION=1 uv run pytest` — only when verifying graphrag/MCP against live services.

## Local supplement

@./CLAUDE.local.md
