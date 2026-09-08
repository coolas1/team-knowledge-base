# Architecture

A team knowledge-base application deployed as **one app**: a Python backend
process (BFF + engine + plugin in-process) plus a TypeScript agent sidecar,
backed by Postgres+pgvector and Neo4j. Hindsight's memory features are
*capability flags* on the single engine, not a separate stack.

```
browser ──► SPA (src/tkb/client)
   │  /api/*                     /mcp
   ▼                               ▼
tkb BFF (src/tkb/server)      MCP server (src/agent/tkb/mcp)
   │  in-process                    │  in-process
   ▼                                ▼
engine (src/engine)  ◄──────────────┘
   └─ GraphRAGBackend (+ optional memory capabilities)

pi-agent sidecar (src/tkb/agent, TS) ──MCP──► backend:8000/mcp
BFF /api/agent/* ──HTTP proxy──► pi-agent:8010
```

## Vocabulary

| Term | Meaning |
|---|---|
| **engine** | The knowledge base: storage, retrieval, ingestion. A library + CLI. No agents, no MCP. |
| **plugin** | A loadable folder package of skills + hooks + MCP server that wraps the engine. Stateless. |
| **app** (`tkb`) | The deployable: BFF + SPA + agent sidecar. Owns the process and the wiring. |
| **skill** | A named procedure an agent can execute (search_and_answer, ingest_and_summarize, reflective_search). |
| **hook** | Policy attached to tool execution - approval gates, expressed as data, never callbacks. |

## Modules

- **`src/engine`** - GraphRAG knowledge base. `graphrag/backend.py` implements
  the `KnowledgeBase` Protocol (`interface.py`); `memory/` holds the
  retain/recall/reflect capabilities described below. Selected by
  `engine.impl` (only `graphrag` exists; the dispatch is the extension point).
- **`src/plugin`** - stateless agent-facing seam. `PluginLoader` reads
  `plugin.yaml` + `skills/` + `hooks/`; `HookPolicy` evaluates hook YAML as
  data. One plugin (`tkb`), selected by `plugin.impl`.
- **`src/tkb`** - the app. `server/` is the FastAPI BFF (all API under `/api`,
  MCP mounted at `/mcp`, SPA served from `client/dist`); `client/` is the
  React SPA; `agent/` is the pi-agent sidecar. `server/deps.py:startup()` is
  the single wiring path: `init_db` -> `build_engine` -> optional query
  service -> load plugin -> `build_llm` -> MCP `set_*` -> optional graph
  worker.

## Memory capabilities

Memory is configured on the engine, not chosen as an engine:

| Flag | Default | Gates |
|---|---|---|
| `engine.memory.enabled` | `false` | retain hook at index time; reflective query service (`engine/memory/query.py`); memory MCP tools (`query_knowledge`, `search_knowledge_fast`, `search_knowledge_deep`) |
| `engine.memory.graph_worker` | `true` | the outbox -> Neo4j projection worker (started by the app lifespan) |
| `engine.memory.retain_max_concurrent` | `1` | retain pipeline concurrency |

`memory.enabled=false` is the plain GraphRAG app. With it on, documents gain
`memory_*` fields (enriched in `graphrag/backend.py`, failure-isolated), the
reflective query service is wired, and the memory MCP tools register - never
registered-but-broken. `search` falls back to plain recall when memory is off.
The worker additionally honors the `HINDSIGHT_GRAPH_WORKER_*` env kill switch.

## Surfaces

| Surface | Path | Backed by |
|---|---|---|
| SPA | `/` (fallback to `index.html`) | `src/tkb/client` build |
| REST API | `/api/documents`, `/api/search`, `/api/graph/*`, `/api/config`, `/api/agent/*` | engine in-process; agent proxy -> pi-agent |
| MCP | `/mcp` | `src/agent/tkb/mcp` (single tool surface; hook-guarded `remove_document`) |
| Health | `/health` | - |
| CLI | `python -m src.engine.cli` | `build_engine(engine_config_from_app(...))` - honors `engine.memory` |

## Config boundaries

| Source | Selects |
|---|---|
| `config/app.yaml` -> `config/schema.py` (`AppConfig`) | `engine.impl`, `engine.memory.*`, `plugin.impl` |
| `.env` -> `config/settings.py` | infra creds, `LLM_*`, `RERANKER_*`, graph-worker kill switch |
| `config/engine/graphrag/entity_schema.yaml` | entity/relation extraction schema |
| `src/agent/tkb/plugin.yaml` | skills, hooks, MCP endpoint |

## Deployment

One backend container (`Containerfile`: uvicorn `src.frontend.webapp.server.app:app`
:8000, SPA built in-place) + pi-agent sidecar (`src/extensions/pi-agent/Containerfile`,
:8010, `TKB_MCP_URL` -> `backend:8000/mcp`) + Postgres + Neo4j. Ollama is
opt-in (`--profile ollama`). Dev mirrors prod: one uvicorn, Vite
`npm run dev`, pi-agent dev script.

## Error handling

- Memory is additive: retain failures set `memory_status` on the document but
  never fail ingestion; recall falls back to plain GraphRAG when no query
  service is wired; the graph worker retries under lease/attempt caps and its
  failure never kills the app.
- MCP surface honesty: memory tools are registered iff a query service exists.
- Startup fails fast on config errors (bad `engine.impl`, unreachable DB).
