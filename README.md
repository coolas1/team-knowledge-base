# Team Knowledge Base

Three-module architecture: `src/{engine,agent,frontend}/`. See
`docs/specs/three-module-refactor-spec.md`.

## Services (Docker)

Backing services for the graphrag engine - Postgres+pgvector and Neo4j - are
managed by `docker-compose.yml`. Ollama is external (set `OLLAMA_BASE_URL` in
`.env`; copy `.env.example` to `.env` first). All credentials/ports live in `.env`,
which both compose and the app read.

    cp .env.example .env            # then edit .env (esp. OLLAMA_BASE_URL)
    docker compose up -d            # starts kb-postgres (:5433) + kb-neo4j (:7687/:7474)
    docker compose ps               # wait for both to be "healthy"
    docker compose down             # stop (add -v to wipe data volumes)

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
