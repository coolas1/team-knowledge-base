# Team Knowledge Base

Three-module architecture: `src/{engine,agent,frontend}/`. See
`docs/specs/three-module-refactor-spec.md`.

## Run (Phase 1 - engine)

CLI:
    uv run python -m src.engine.cli ingest --name report.md --data "$(cat report.md)"
    uv run python -m src.engine.cli recall --query "acme"
    uv run python -m src.engine.cli graph

MCP server (streamable HTTP at http://localhost:8000/mcp):
    uv run python -m src.engine.mcp

## Tests

    uv run pytest                      # unit + fake-backed contract tests
    RUN_INTEGRATION=1 uv run pytest    # also runs graphrag against live services
