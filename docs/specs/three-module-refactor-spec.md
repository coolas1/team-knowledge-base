# Three-Module Refactor — Spec & Execution Plan

Status: **spec** (planning). Branch: `refactor/three-module`.
This document is the agreed basis for the clean-break refactor. It supersedes the
earlier stashed `scaffold-refactor-design.md`.

## 1. Goals & principles

Clean-break refactor of the team-knowledge-management app into **three modules** under
`src/{engine,agent,frontend}/<implementation>/`. Each module exposes a **stable interface
contract** and hosts **swappable implementations selected by config**.

Principles (from brainstorming):
- **Clean break.** Old `src/{api,core,pipeline,db}` is dissolved into the new structure;
  the app is rebuilt, not incrementally wrapped.
- **One-way dependencies.** `frontend` → `agent` + `engine` (as clients); `agent` → `engine`
  (via CLI/MCP); `engine` depends on nothing above it.
- **Engine = no agents.** Fast, mostly-deterministic code; LLM/embedding only for embeddings,
  chunk summaries, and request rewriting.
- **Agent = a plugin to a switchable harness** (Codex first, since there is no harness to
  test against yet; any other harness is also fine).
- **Monorepo, polyglot.** Modules need not be Python (frontend may be Vue, etc.).
- **Memory deferred.** The agent decides where its memory lives; no concrete store is built
  in this effort.
- **Both deployment modes supported:** all-in-one (in-process) and distributed (MCP/CLI).

## 2. Out of scope (this effort)

- Multi-tenancy / auth / teams (the `hstest` branch work) — design interfaces so it can
  layer on later.
- Agent retrieval-augmented **file generation/modification** (feature "c") — later phase.
- A second engine backend (e.g. `wiki`) — interface only; `graphrag` is the sole impl.
- `windowsapp` frontend — stub only.
- Concrete memory store implementation.

## 3. Target directory structure

```
src/
  engine/                     # Module 1 — Knowledge Base Engine
    interface.py              # KnowledgeBase Protocol + Capabilities + types (THE contract)
    config.py                 # EngineConfig + build_engine() factory (selects impl)
    cli.py                    # CLI adapter (thin; wraps a KnowledgeBase instance)
    mcp.py                    # MCP server adapter (thin; wraps same instance)
    components/               # shared building blocks (migrated from old src/)
      extractors/             #   pdf/docx/pptx/markdown/image + registry
      chunker.py
      embedder.py             #   embedding providers (small-model)
      reranker.py
      bm25.py                 #   lexical recall (from feature branch)
      query_rewriter.py       #   small-LLM request rewrite (from feature branch)
      store/                  #   vector (pgvector), graph (neo4j), document store
    graphrag/                 # impl: GraphRAG (the only impl for now)
      backend.py              #   implements KnowledgeBase
      pipeline.py             #   ingest/reingest orchestration (chunk-level graph update)
    wiki/                     # impl: stub/future
      README.md               #   placeholder; not built this effort
  agent/                      # Module 2 — Agentic Flow (plugin to a harness)
    interface.py              # Skill, AgentRunner, EngineClient, AgentPlugin contracts
    engine_client.py          # uniform client over CLI/MCP transport
    skills/                   # harness-agnostic skill logic (shared, callable in-process)
      search_and_answer.py    #   (a) recall + answer
      ingest_and_summarize.py #   (b) ingest a file, produce a summary
    memory.py                 # Memory abstraction — interface only, impl DEFERRED
    codex/                    # impl: codex harness plugin
      plugin.py               #   wraps shared skills into codex skill/command format + config
  frontend/                   # Module 3 — GUIs (polyglot; one impl per client)
    webapp/                   # impl: web app
      server/                 #   BFF gateway (FastAPI): calls engine + agent skills
      client/                 #   SPA (Vue 3 preferred; React equally viable — see §9)
    windowsapp/               # impl: stub/future
      README.md
config/
  app.yaml                    # top-level: selects engine/agent/frontend impl
  schema.py                   # pydantic AppConfig (validates app.yaml)
  engine/graphrag/            # graphrag impl config (entity_schema.yaml, model_config.yaml move here)
  agent/codex/                # codex plugin config
docs/specs/three-module-refactor-spec.md   # this file
```

`src/engine/interface.py`, `src/agent/interface.py`, and shared `components/`/`skills/`
live at **module level**; implementation subdirs (`graphrag/`, `codex/`, `webapp/`) hold
the swappable parts. This matches `src/{engine,agent,frontend}/<implementation>/`.

## 4. Module contracts

### 4.1 Engine — `src/engine/interface.py`

```python
class KnowledgeBase(Protocol):
    capabilities: Capabilities
    async def ingest(self, source: IngestSource) -> DocumentRef: ...      # create
    async def reingest(self, doc_id: str) -> DocumentRef: ...             # partial update
    async def remove(self, doc_id: str) -> None: ...                      # delete
    async def recall(self, request: RecallRequest) -> RecallResult: ...   # vector + graph
    async def get_graph(self, entity: str | None = None) -> GraphData: ...        # optional
    async def get_neighbors(self, entity: str) -> GraphData: ...                  # optional
```

- `Capabilities` declares what a backend supports (graph, partial update, multi-modal,
  namespace); optional methods raise `NotSupported`.
- `build_engine(EngineConfig)` (in `config.py`) imports `src.engine.<impl>.backend:build`.
- `cli.py` and `mcp.py` are **adapters only** — no business logic; both wrap the same
  `KnowledgeBase` instance. MCP is the cross-process interface (for the codex agent);
  the in-process library is used by the webapp BFF.

### 4.2 Agent — `src/agent/interface.py`

```python
class Skill(Protocol):
    name: str
    description: str
    async def run(self, ctx: SkillContext) -> SkillResult: ...

class EngineClient(Protocol):   # uniform over CLI/MCP; skills call this, not engine internals
    async def recall(self, query: str, top_k: int = 10) -> dict: ...
    async def ingest(self, path: str, ...) -> dict: ...
    ...

class AgentPlugin(Protocol):    # what each harness impl exposes
    harness: str
    def skills(self) -> list[Skill]: ...
```

- Skills live in `src/agent/skills/` and are **harness-agnostic** — callable in-process
  (by the webapp BFF) and wrappable by any harness plugin.
- `src/agent/codex/plugin.py` packages the shared skills into the codex harness format.
- `src/agent/memory.py` defines a `MemoryStore` Protocol **only**; no implementation. The
  agent decides its storage at runtime (deferred). **No coupling to the engine.**

### 4.3 Frontend — `src/frontend/<impl>/`

Each implementation is a full client. `webapp` = `server/` (BFF) + `client/` (SPA). The BFF
calls the engine (in-process by default, MCP when distributed) and may invoke agent skills
in-process for features like "ask" and "ingest + summarize". Graphical operations map to
engine calls: open folder → `ingest` each file; drag file in → `ingest`; drag to dustbin →
`remove`. The BFF also exposes config read/modify.

## 5. Config system

`config/app.yaml` selects implementations and wiring; `config/schema.py` validates it.

```yaml
engine:
  impl: graphrag               # graphrag | wiki | ...
  config: config/engine/graphrag
agent:
  harness: codex               # codex | <other>
  skills: [search_and_answer, ingest_and_summarize]
  memory: { impl: null }       # deferred
frontend:
  impl: webapp
webapp:
  engine_access: inprocess     # inprocess | mcp
```

Existing `config/entity_schema.yaml` and `config/model_config.yaml` move under
`config/engine/graphrag/`.

## 6. Milestone 1 scope (what gets built)

- **Engine:** `interface.py` + `components/` (migrated from old `src/`) + `graphrag/` impl +
  `cli.py` + `mcp.py` + `config.py`. Old engine code in `src/{core,pipeline,db}` removed.
- **Agent:** `interface.py` + `engine_client.py` + skills (a)+(b) + `codex/` plugin +
  `memory.py` (interface only).
- **Frontend:** `webapp/` — BFF (documents/search/graph/ingest/config + agent skill
  invocation) + SPA (browse, search, graph view, ingest via drag/folder).
- **Config:** `app.yaml` + `schema.py` + per-impl config files.

## 7. Phased execution plan

| Phase | Scope | Exit criteria |
|-------|-------|---------------|
| 0 | Spec (this) + branch | Spec reviewed |
| 1 | Engine: interface + components + graphrag + CLI + MCP; remove old engine code | `kb ingest/recall/graph` via CLI works; MCP serves tools; old `src/{core,pipeline,db,api/mcp_server}` gone |
| 2 | Agent: interface + engine_client + skills (a)+(b) + codex plugin | Skills run in-process; codex plugin loads and calls engine via MCP |
| 3 | Frontend: webapp BFF + SPA | End-to-end: browse/search/graph/ingest; invoke agent skills from UI |
| 4 (later) | File generation (c); memory impl; wiki backend; windowsapp; multi-tenancy | — |

Each phase ends with the previous `src/` code for that concern removed (clean break).

## 8. Migration map (old → new)

| Old                          | New                                            |
|------------------------------|------------------------------------------------|
| `src/pipeline/extractors/`   | `src/engine/components/extractors/`            |
| `src/pipeline/{chunker,embedder,analyzer,pipeline}.py` | `src/engine/components/` + `src/engine/graphrag/` |
| `src/core/{knowledge_base,search}.py` | `src/engine/graphrag/{backend,pipeline}.py`    |
| `src/core/reranker.py`       | `src/engine/components/reranker.py`            |
| `src/core/{bm25_index,query_rewriter}.py` | `src/engine/components/{bm25,query_rewriter}.py` |
| `src/db/`                    | `src/engine/components/store/`                 |
| `src/api/mcp_server.py`      | `src/engine/mcp.py`                            |
| `src/api/routes.py`          | `src/frontend/webapp/server/` (BFF)            |
| `src/main.py`                | split: `src/engine/{cli,mcp}.py` + `src/frontend/webapp/server/` |
| `frontend/`                  | `src/frontend/webapp/client/` (rebuilt; SPA tech TBD) |
| `config/{entity_schema,model_config}.yaml` | `config/engine/graphrag/`          |

## 9. Open confirm-points

1. **Frontend SPA tech:** Vue 3 + Vite + TS (preferred per your note) vs. keep React+Vite+TS.
   The BFF/architecture is framework-agnostic, so only `client/` impl differs.
2. **Monorepo tooling:** `uv` workspaces for the Python parts + `pnpm` for frontend? (Old
   `frontend/` used npm.) Confirm or override.

Once these two are confirmed, Phase 1 (engine) can start.
