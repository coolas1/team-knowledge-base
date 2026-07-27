# Team Knowledge Base

Three-module architecture: `src/{engine,agent,frontend}/`. See
`docs/specs/three-module-refactor-spec.md`.

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
