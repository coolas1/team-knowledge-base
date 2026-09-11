# team-knowledge-base

A GraphRAG-powered team knowledge base: ingest documents into a three-layer
knowledge graph (entities → relations → chunks), retrieve via semantic search
with reranking, and query through a CLI, an MCP server, or a web UI.

Three independently switchable modules live under `src/`:

- **engine** — GraphRAG storage/retrieval (Postgres+pgvector, Neo4j), CLI.
- **agent** — plugin-based skills + LLM orchestration (see `src/agent/CLAUDE.md`).
- **frontend** — FastAPI BFF + React SPA (see `src/frontend/CLAUDE.md`).

## Commands

Run from the repo root unless noted. Python tooling uses `uv`.

- **Install deps:** `uv sync` (add `--extra reranker` only for a local torch reranker)
- **Run tests:** `uv run pytest`
- **Integration tests:** `RUN_INTEGRATION=1 uv run pytest` (live Postgres+Neo4j+Ollama)
- **Lint:** `uv run ruff check`
- **Format:** `uv run ruff format`
- **Engine CLI:** `uv run python -m src.engine.cli recall --query "..."`
- **BFF server:** `uv run uvicorn src.frontend.webapp.server.app:app --reload` (serves `/mcp`)
- **SPA (dev):** `cd src/frontend/webapp/client && npm install && npm run dev` (proxies `/api` → :8000)
- **SPA tests:** `cd src/frontend/webapp/client && npm test`

## Workflow

### Branches and releases

Two long-lived branches on `origin`:

- `main` — the stable, versioned branch the LAN pipeline watches and deploys.
  Nothing lands here except via a maintainer-owned release.
- `develop` — the integration branch every collaborator branches from.

A change is a feature branch off `develop`; its PR carries the spec delta plus
the code. After a PR merges to `develop`, the maintainer archives the change —
fold the delta into `specs/` and move the change into `archive/` — with a direct
push to `develop`. Archive-after-merge is the maintainer's job, once per merge,
never the PR author's.

Releases are maintainer-owned and batched: the maintainer merges `develop` into
`main`, bumps the version, and tags it. The version lives in the repo-root
`VERSION` file (the app reads it at runtime) and is mirrored in
`pyproject.toml`; bump both together. The runbook:

```bash
NEW_VERSION=0.3.0
git checkout main
git pull --ff-only origin main
git merge --ff-only origin/develop
printf '%s\n' "$NEW_VERSION" > VERSION
sed -i 's/^version = ".*"/version = "'"$NEW_VERSION"'"/' pyproject.toml
git add VERSION pyproject.toml
git commit -m "chore(release): bump version to $NEW_VERSION"
git tag "v$NEW_VERSION"
git push origin main --tags
```

The pipeline redeploys `main` within ~5 min; confirm the deployed `GET /version`
reports the new version plus the deploy SHA.

One-time bootstrap — fast-forward `origin/develop` to `origin/main` so both
start from the same head (run once, before anyone branches from `develop`):

```bash
git fetch origin
git branch -f develop origin/main
git push origin develop
```

### Day-to-day

1. Run `uv run ruff check` and `uv run pytest` before pushing.
2. **The LAN deployment is pipeline-managed** (`cicd/`): a systemd user timer
   polls `origin/main` every 5 min, gates on lint + tests, builds SHA-tagged
   images, and redeploys via `podman compose`. Do NOT run
   `docker/podman compose up` by hand — the pipeline is the sole operator of
   the `team-kb` compose project; use the published ports (5433/7687/8000)
   as a client instead. Runbook, rollback, and install steps: `cicd/README.md`.
3. Local dev backing services (`docker compose up -d` in the dev checkout)
   are separate from the LAN deployment; copy `.env.example` to `.env` and
   set `EMBEDDING_BASE_URL` and `LLM_BASE_URL` first.
4. The reranker is configurable via `RERANKER_PROVIDER`: `http` (external
   `/v1/rerank` API, default), `local` (torch — needs `--extra reranker`), or `none`.
5. Memory capabilities (retain, reflective query, graph worker) toggle via
   `engine.memory.*` in `config/app.yaml`.
6. Ingest parallelism is bounded by `engine.ingest.chunk_concurrency` and
   `engine.ingest.doc_concurrency` in `config/app.yaml` (1 = serial).

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
Neo4j (entity/relation graph). Ollama is opt-in via the compose `ollama`
profile. Config flows through `.env` → `config/settings.py` (pydantic-settings).

## Validity check

- `uv run pytest` — unit + contract + BFF tests (must pass).
- `cd src/frontend/webapp/client && npm test` — SPA api-client tests.
- `RUN_INTEGRATION=1 uv run pytest` — only when verifying graphrag/MCP against live services.

## Local supplement

@./CLAUDE.local.md
