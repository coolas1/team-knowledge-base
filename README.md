# Team Knowledge Base

A GraphRAG-powered knowledge base for teams. Ingest documents (PDF, DOCX, PPTX,
Markdown, CSV, images) and the engine builds a **three-layer knowledge graph** -
entities, relations, and text chunks - indexed for semantic search with
reranking. Query it through a CLI, an MCP server, or a web UI. Deployed as one
app, with optional memory capabilities (reflective search, memory graph).

## Features

- **GraphRAG retrieval** - vector search (Postgres + pgvector) over a knowledge
  graph (Neo4j) of extracted entities and relations.
- **Multi-format ingestion** - PDF, DOCX, PPTX, Markdown, CSV, and image (OCR) extractors.
- **Pluggable reranker** - external `/v1/rerank` API (default), a local
  CrossEncoder (optional, torch), or none.
- **Memory capabilities** - toggleable via `engine.memory.*` in
  `config/app.yaml`: retain pipeline, reflective query, Neo4j memory-graph
  worker. Off by default; on = the full reflective stack.
- **Four interfaces** - web UI (SPA + BFF), MCP server (mounted at `/mcp`),
  CLI (`src.engine.cli`), and a pi-agent chat sidecar.
- **Single-app deployment** - one backend container + pi-agent sidecar +
  Postgres/Neo4j (Ollama optional).

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
- Node.js (for the SPA and pi-agent)
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
   cp .env.example .env          # then edit, especially OLLAMA_BASE_URL
   ```
3. Start backing services (Postgres+pgvector, Neo4j):
   ```bash
   docker compose up -d          # team-kb-postgres :5433, team-kb-neo4j :7687/:7474
   docker compose ps             # wait until both are "healthy"
   ```

## Usage

### Run the full stack containerized

```bash
docker compose up -d --build
docker compose logs -f backend
open http://localhost:8000      # SPA + /api/* + /mcp + /health
```

The backend (BFF + engine + plugin, one process) runs on :8000; the pi-agent
sidecar on :8010 (chat with it via `node src/extensions/pi-agent/scripts/chat.mjs`).
Ollama is opt-in: append `--profile ollama` to run a bundled Ollama, otherwise
the services use the LLM/embedding endpoints from `.env`. The reranker reuses
your host HuggingFace cache (`BAAI/bge-reranker-v2-m3` must be cached) via the
compose volume mount.

### Run the app on the host

```bash
# App server: BFF + engine + plugin (port 8000, mounts /mcp)
uv run uvicorn src.frontend.webapp.server.app:app --reload

# Engine CLI
uv run python -m src.engine.cli recall --query "acme"

# SPA (port 5173, proxies /api -> :8000)
cd src/tkb/client && npm install && npm run dev

# pi-agent sidecar (port 8010)
cd src/tkb/agent && npm ci && npm run build && npm start
```

### Toggle memory capabilities

`config/app.yaml`:

```yaml
engine:
  memory:
    enabled: true       # retain + reflective query + memory MCP tools
    graph_worker: true  # Neo4j memory-graph projection worker
```

### Tests

```bash
uv run pytest                                # unit + contract + BFF tests
cd src/tkb/client && npm test                # SPA api-client tests
cd src/tkb/agent && npm run check            # pi-agent typecheck + tests
RUN_INTEGRATION=1 uv run pytest              # graphrag + MCP vs live services
```

## Contributing

Development conventions, commands, and architecture notes live in
[`CLAUDE.md`](CLAUDE.md) (mirrored to Codex and other harnesses via the tracked
`AGENTS.md` symlink). Quick rules:

- Lint and test before pushing: `uv run ruff check && uv run pytest`.
- Follow Conventional Commits, scoped to the module touched - for example
  `feat(engine): ...`, `fix(plugin): ...`, `refactor(tkb): ...`.
