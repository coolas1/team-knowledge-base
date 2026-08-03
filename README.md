# Team Knowledge Base

A GraphRAG-powered knowledge base for teams. Ingest documents (PDF, DOCX, PPTX,
Markdown, images) and the engine builds a **three-layer knowledge graph** —
entities, relations, and text chunks — indexed for semantic search with
reranking. Query it through a CLI, an MCP server, or a web UI.

Includes a built-in benchmark harness for comparing retrieval and file-update
performance against [gbrain](https://github.com/garrytan/gbrain) (PGLite hybrid
RAG) and [WeKnora](https://github.com/Tencent/WeKnora) (multimodal RAG stack).

## Features

- **GraphRAG retrieval** — vector search (Postgres + pgvector) over a knowledge
  graph (Neo4j) of extracted entities and relations.
- **Multi-format ingestion** — PDF, DOCX, PPTX, Markdown, and image (OCR) extractors.
- **Pluggable reranker** — external `/v1/rerank` API (default), a local
  CrossEncoder (optional, torch), or none.
- **Three interfaces** — CLI (`src.engine.cli`), MCP server (`src.engine.mcp`),
  and a FastAPI BFF + React SPA.
- **Containerized** — single `docker-compose.yml` for the full stack.
- **Cross-system benchmark** — 40 QA + 20 file-update eval against gbrain and
  WeKnora, all sharing the same test corpus.

## Quick start

```bash
git clone https://github.com/Cried1/team-knowledge-base.git
cd team-knowledge-base
cp .env.example .env          # edit OLLAMA_BASE_URL and other settings
docker compose up -d --build  # postgres + neo4j + webapp on :8000
open http://localhost:8000
```

## Usage

### App

```bash
# Full stack containerized
docker compose up -d --build

# Host development (backing services in Docker, app on host)
docker compose up -d postgres neo4j
uv run uvicorn src.frontend.webapp.server.app:app --reload
```

### Benchmark

The benchmark harness compares three PKM systems against the same corpus
(`bench/raw/`) and eval suites (`bench/eval/`). Each system gets its own
ingest pipeline and is scored independently.

| System | Description | Status |
| --- | --- | --- |
| `team-knowledge-base` | FastAPI + Neo4j + pgvector (this project) | Root `docker-compose.yml` |
| `gbrain` | PGLite hybrid RAG baseline | `bench/containers/gbrain/compose.yml` |
| `weknora` | WeKnora multimodal RAG (Tencent) | `bench/containers/weknora/compose.yml` |

**Run all three:**

```bash
# 1. Start team-knowledge-base
docker compose up -d --build

# 2. Ingest the shared corpus into tkb
bash bench/scripts/team-knowledge-base/run-all.sh

# 3. Run benchmarks against tkb
uv run python bench/scripts/run-qa-bench.py --pkm team-knowledge-base
uv run python bench/scripts/run-file-update-bench.py --pkm team-knowledge-base

# 4. Start gbrain (see bench/docs/GBRAIN_INSTALL.md)
docker compose -f bench/containers/gbrain/compose.yml up -d --build
bash bench/scripts/gbrain/run-all.sh
uv run python bench/scripts/run-qa-bench.py --pkm gbrain

# 5. Start WeKnora (see bench/docs/WEKNORA_INSTALL.md)
docker compose -f bench/containers/weknora/compose.yml up -d --build
bash bench/scripts/weknora/run-all.sh
uv run python bench/scripts/run-qa-bench.py --pkm weknora
```

**Benchmark flags:**

| Flag | Effect |
| --- | --- |
| `--pkm NAME` | Target a single PKM (default: all in bench.yaml) |
| `--skip-ingest` | Skip reset+ingest, assume KB is populated |
| `--skip-predict` | Score existing predictions only |
| `--skip-eval` | Generate predictions only, no scoring |
| `--parallel N` | Max concurrent prediction agents (QA only, default: 4) |
| `--timeout SECS` | Per-agent timeout (default: 600) |

### Corpus

The `bench/raw/` directory is a synthetic multi-modal personal archive spanning
six threads: research, music, fermentation, travel, journal, and notes. It
includes Markdown, PDF, XLSX, CSV, PNG, and JPG files in English, Japanese, and
Chinese. The corpus is **read-only** — all benchmark output goes to
`*-files/` directories (gitignored).

The canonical map of the corpus — file inventory, relations, and suggested
queries — lives in `bench/eval/STORY.md`.

### Tests

```bash
uv run pytest                                   # unit + contract + BFF tests
cd src/frontend/webapp/client && npm test       # SPA api-client tests
RUN_INTEGRATION=1 uv run pytest                 # graphrag + MCP vs live services
```

## Project structure

```
src/                          # Application code
├── engine/                   # GraphRAG engine (CLI, MCP, extractors, chunker, embedder, graphrag)
├── agent/                    # LLM orchestration + skills
└── frontend/                 # FastAPI BFF + React SPA
config/                       # App config (pydantic-settings)
tests/                        # Unit + contract + BFF + integration tests
bench/                        # Benchmark harness
├── eval/                     # QA (40 questions) + file-update (20 entries)
├── raw/                      # READ-ONLY test corpus (shared across all systems)
├── scripts/                  # Orchestrator + per-system ingest/health/reset
│   ├── bench_harness/        # Python library
│   ├── bench.yaml            # PKM registry (gbrain, weknora, team-knowledge-base)
│   ├── run-qa-bench.py       # QA benchmark orchestrator
│   ├── run-file-update-bench.py  # File-update benchmark orchestrator
│   ├── team-knowledge-base/  # tkb ingest/health/reset scripts
│   ├── gbrain/               # gbrain ingest/health/reset scripts
│   └── weknora/              # WeKnora ingest/health/reset scripts
├── containers/               # gbrain + WeKnora compose files
│   ├── gbrain/
│   └── weknora/
└── docs/                     # Install guides (GBRAIN, WEKNORA, TEAM_KNOWLEDGE_BASE)
```

## Contributing

Development conventions, commands, and architecture notes live in
[`CLAUDE.md`](CLAUDE.md) (mirrored to Codex and other harnesses via the tracked
`AGENTS.md` symlink). Quick rules:

- Lint and test before pushing: `uv run ruff check && uv run pytest`.
- Follow Conventional Commits, scoped to the module touched — for example
  `feat(engine): ...`, `fix(webapp): ...`, `refactor(config): ...`.
