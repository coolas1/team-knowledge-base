# Team Knowledge Base

Three-module architecture: `src/{engine,agent,frontend}/`. See
`docs/specs/three-module-refactor-spec.md`.

## Services (Docker)

Backing services for the graphrag engine - Postgres+pgvector and Neo4j - are
managed by `docker-compose.yml`. Ollama is external (set `OLLAMA_BASE_URL` in
`.env`; copy `.env.example` to `.env` first). All credentials/ports live in `.env`,
which both compose and the app read.

    cp .env.example .env            # then edit .env (esp. OLLAMA_BASE_URL, HF_HOME_HOST)
    docker compose up -d            # starts kb-postgres (:5433) + kb-neo4j (:7687/:7474)
    docker compose ps               # wait for both to be "healthy"
    docker compose down             # stop (add -v to wipe data volumes)

## Run (containerized webapp)

Build the webapp image (BFF + built SPA, served together on :8000) and bring up
the whole stack. The webapp connects to Postgres/Neo4j over the compose network
and to Ollama at `OLLAMA_BASE_URL`. The reranker reuses your host HuggingFace
cache via `HF_HOME_HOST` (the `BAAI/bge-reranker-v2-m3` model must be cached
there - it is, if the host app has run a search).

    podman compose up -d --build    # builds team-kb-webapp + starts all services
    podman compose logs -f webapp   # BFF startup creates the DB schema (init_db)
    open http://localhost:8000      # SPA + /api/* + /health

Replace `podman` with `docker` if preferred. To run only the backing services and
develop the app on the host, use the "Run" section below instead.

## Run

Engine MCP server (port 8000, /mcp):
    uv run python -m src.engine.mcp

Engine CLI:
    uv run python -m src.engine.cli recall --query "acme"

Webapp BFF (port 8000):
    uv run uvicorn src.frontend.webapp.server.app:app --reload

Webapp SPA (port 5173, proxies /api -> :8000):
    cd src/frontend/webapp/client && npm install && npm run dev

## Tests

    uv run pytest                                  # all unit + contract + BFF tests
    cd src/frontend/webapp/client && npm test      # SPA api-client tests
    RUN_INTEGRATION=1 uv run pytest                # graphrag + MCP round-trip vs live services
