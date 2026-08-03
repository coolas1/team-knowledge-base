# team-knowledge-base install guide (Linux)

RAG Agent Bench evaluates **team-knowledge-base** — the authors' own PKM app
("园区团队知识库系统") — alongside gbrain and WeKnora against the `raw/` corpus.
Unlike gbrain (an external binary) and WeKnora (a Compose stack),
team-knowledge-base is vendored as a **git submodule** at
`vendors/team-knowledge-base`: its source lives in this tree so it can be developed
and benchmarked in lockstep, with the goal of building a better PKM app.

It is a FastAPI monolith with a React/Vite frontend: multi-modal file ingest, a
"three-layer funnel" retriever (vector recall → reranker gate → graph-augmented
LLM answer with citations), a Neo4j knowledge graph, a REST API, and a co-hosted
MCP server that shares the same `KnowledgeBase` core.

## Prerequisites

| Tool | Why | Install |
|---|---|---|
| **git** | Init the submodule | system package manager |
| **Docker** (+ Compose) | Postgres 16 + pgvector | docker.com |
| **uv** | Python env/deps (repo pins `uv.lock`) | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| **Node ≥ 18** | Frontend dev build | nodejs.org |
| **Neo4j 5** | Knowledge graph store | a running instance on `:7687` (see step 4) |
| **Ollama** | Embedding model `nomic-embed-text` | remote host in this bench — see step 3 |

## 1. Init the submodule

```bash
git submodule update --init vendors/team-knowledge-base
cd vendors/team-knowledge-base
```

> The submodule is pinned to commit `5d96b93`. That commit must exist on
> `origin/main` for `git submodule update` to resolve — if it errors with the
> commit not found, the project owners need to `git push origin main` first.

## 2. Configure `.env`

```bash
cp .env.example .env
# set LLM_API_KEY=<your DashScope key>  (qwen-turbo; see config/model_config.yaml)
```

The defaults already match the docker-compose services (`POSTGRES_*`, `NEO4J_*`).
The app listens on `APP_HOST=0.0.0.0 APP_PORT=8000`.

## 3. Configure models (`config/model_config.yaml`)

`config/model_config.yaml` is hot-swapped at runtime — no code changes to switch:

- **embedding** — default `ollama:nomic-embed-text` (768d). Change `base_url` from
  `http://localhost:11434` to the bench's **remote Ollama**
  `http://10.201.186.15:11434` (verify: `curl http://10.201.186.15:11434/api/tags`).
- **gatekeeper** — `sentence-transformers` `BAAI/bge-reranker-v2-m3`
  (threshold `0.01`, `top_n: 10`). First run downloads the model.
- **llm** — `qwen-turbo` via DashScope (OpenAI-compatible); needs `LLM_API_KEY`.

> Switching the embedding model changes the vector dimension — re-embed all chunks
> and recreate the HNSW index (see `docs/specs/design.md`).

## 4. Bring up the stores

```bash
docker compose up -d          # pgvector/pgvector:pg16 on host :5433
```

Neo4j must already be running at `bolt://localhost:7687`. The design reuses an
existing container; in this workspace, confirm a Neo4j 5 is up (WeKnora's stack may
provide one) and match `NEO4J_URI` / `NEO4J_USER` / `NEO4J_PASSWORD` in `.env`.

## 5. Install deps & run

```bash
uv sync                                                          # Python deps
uv run uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload  # API + MCP
```

Health check: `curl http://localhost:8000/health` → `{"status":"ok"}`.

Frontend (separate terminal):

```bash
cd frontend && npm install && npm run dev   # Vite dev server, default :5173
```

## REST + MCP surface

REST (`src/api/routes.py`): `POST /documents/upload`,
`PUT /GET /DELETE /documents[/{doc_id}]`, `POST /search`,
`GET /graph/{full,entity/{name},neighbors/{name}}`.

MCP tools (`src/api/mcp_server.py`): `search`, `get_document`, `query_graph`,
`upload_document` — same `KnowledgeBase` core as REST, zero duplication.

The MCP server is mounted at **`/mcp`** on the FastAPI app →
`http://127.0.0.1:8000/mcp` (streamable HTTP). Note: the app's own `.mcp.json`
lists port `8001`, which is stale — MCP is served on `APP_PORT` (8000).

## Ingesting the corpus (bench adapter pending)

team-knowledge-base ingests **one file at a time** via `POST /documents/upload` —
there is no bulk/dir CLI yet. A `scripts/team-knowledge-base/ingest.sh` that walks
`raw/` and uploads each file is the remaining onboarding step. Until then, per-file:

```bash
curl -F "file=@raw/notes/some-note.md" http://localhost:8000/documents/upload
```

Corpus rules the adapter must honor (see `CLAUDE.md` / `eval/STORY.md`):

- **`raw/` is read-only** — upload reads bytes; never write back.
- **Only `.md` is parsed as text.** PDF/XLSX/CSV/PNG/JPG are leaf nodes described by
  surrounding markdown; for gbrain-parity runs, ingest the `.md` files only. (The
  app *can* OCR/parse binaries, but the shared corpus treats them as prose-described.)
- Bench output should land in `team-knowledge-base-files/` (logs, exports), not the
  app's in-tree `uploads/`.

## What this app brings to the bench

- **Three-layer retrieval** (vector → reranker → graph-augmented LLM answer with
  citations) — richer than gbrain's hybrid, comparable to WeKnora.
- **Neo4j knowledge graph** with a configurable domain entity/relation schema
  (`config/entity_schema.yaml`, LLM-open types).
- **Co-hosted REST + MCP** sharing one core — directly comparable on the agent axis.
- **Multi-modal extractors** (md/pdf/docx/pptx/image-OCR) — broader coverage than
  gbrain's `.md`-only ingest.

## What this bench does NOT wire (yet)

- No `scripts/team-knowledge-base/` pipeline (reset / ingest / health).
- No `team-knowledge-base-files/` output dir.
- No entry in the top-level `.mcp.json` — add once the run/port is confirmed.
