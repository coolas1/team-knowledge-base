# Team Knowledge Base

A GraphRAG-powered knowledge base for teams. Ingest documents (PDF, DOCX, PPTX,
Markdown, images) and the engine builds a **three-layer knowledge graph** —
entities, relations, and text chunks — indexed for semantic search with
reranking. Query it through a CLI, an MCP server, or a web UI.

## Features

- **GraphRAG retrieval** — vector search (Postgres + pgvector) over a knowledge
  graph (Neo4j) of extracted entities and relations.
- **Multi-format ingestion** — PDF, DOCX, PPTX, Markdown, and image (OCR) extractors.
- **Pluggable reranker** — external `/v1/rerank` API (default), a local
  CrossEncoder (optional, torch), or none.
- **Three interfaces** — CLI (`src.engine.cli`), MCP server (`src.engine.mcp`),
  and a FastAPI BFF + React SPA.
- **Containerized** — single-stage Containerfile; compose for backing services.

## Agent document generation

The conversation Agent can generate downloadable Word (`.docx`), PDF, and
PowerPoint (`.pptx`) files. Ask for the desired format and content in the chat;
the Agent retrieves knowledge when needed, calls the `generate_document` MCP
tool, and returns a link under `/api/artifacts/{id}/download`.

PowerPoint requests also produce an editable Slidev Markdown file. Use `---` on
its own line to separate slides, then run the downloaded source with Slidev if
you want to restyle or present it. Generated files are stored in the
`artifactsdata` Compose volume so Webapp container rebuilds do not remove them.

## Installation

### Prerequisites

- Python ≥ 3.12 and [`uv`](https://docs.astral.sh/uv/)
- Node.js (for the SPA)
- Docker or Podman (for backing services)

### Steps

1. Clone and install Python dependencies:
   ```bash
   git clone https://github.com/Cried1/team-knowledge-base.git
   cd team-knowledge-base
   uv sync                       # add --extra reranker only for a local torch reranker
   ```
2. Configure environment:
   ```bash
   cp .env.example .env          # then edit, especially OLLAMA_BASE_URL and HF_HOME_HOST
   ```
3. Start backing services (Postgres+pgvector, Neo4j):
   ```bash
   docker compose up -d          # kb-postgres :5433, kb-neo4j :7687/:7474
   docker compose ps             # wait until both are "healthy"
   ```

## Usage

### Run the app on the host

```bash
# Engine MCP server (port 8000, /mcp)
uv run python -m src.engine.mcp

# Engine CLI
uv run python -m src.engine.cli recall --query "acme"

# Webapp BFF (port 8000)
uv run uvicorn src.frontend.webapp.server.app:app --reload

# Webapp SPA (port 5173, proxies /api -> :8000)
cd src/frontend/webapp/client && npm install && npm run dev
```

### Run the full stack containerized

Builds the webapp image (BFF + built SPA, served together on :8000) and brings
up the whole stack. The reranker reuses your host HuggingFace cache via
`HF_HOME_HOST` (the `BAAI/bge-reranker-v2-m3` model must be cached there).

```bash
podman compose up -d --build    # or: docker compose up -d --build
podman compose logs -f webapp   # BFF startup creates the DB schema (init_db)
open http://localhost:8000      # SPA + /api/* + /health
```

To run only the backing services and develop the app on the host, use the host
run commands above instead.

### Tests

```bash
uv run pytest                                   # unit + contract + BFF tests
cd src/frontend/webapp/client && npm test       # SPA api-client tests
RUN_INTEGRATION=1 uv run pytest                 # graphrag + MCP vs live services
```

## Contributing

Development conventions, commands, and architecture notes live in
[`CLAUDE.md`](CLAUDE.md) (mirrored to Codex and other harnesses via the tracked
`AGENTS.md` symlink). Quick rules:

- Lint and test before pushing: `uv run ruff check && uv run pytest`.
- Follow Conventional Commits, scoped to the module touched — for example
  `feat(engine): ...`, `fix(webapp): ...`, `refactor(config): ...`.
