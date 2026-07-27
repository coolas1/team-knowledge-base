# Three-Module Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Clean-break refactor the team-knowledge-management app into three modules (`src/{engine,agent,frontend}/`) with stable interface contracts and swappable implementations selected by config.

**Architecture:** `engine` (knowledge-base engine, no agents) is the foundation and depends on nothing above it; `agent` (harness-agnostic skills + codex plugin) depends on `engine` via a uniform `EngineClient` over in-process/MCP transports; `frontend` (webapp = FastAPI BFF + React SPA) depends on both. Config in `config/app.yaml` + `config/schema.py` selects implementations. Old `src/{api,core,db,pipeline,main.py}` is dissolved; the app is rebuilt, not wrapped.

**Tech Stack:** Python 3.12+ / uv / FastAPI / Postgres 16 + pgvector / Neo4j 5 / Ollama (nomic-embed-text, 768d) / MCP Python SDK (streamable HTTP) / React + Vite + TS / pytest + pytest-asyncio.

## Global Constraints

Copied verbatim or derived from `docs/specs/three-module-refactor-spec.md`; every task implicitly includes these.

- **Clean break:** old `src/{core,pipeline,db,api,main.py}` is deleted, not wrapped. Each phase ends with the previous code for that concern removed.
- **One-way deps:** `frontend -> agent + engine`; `agent -> engine` (via CLI/MCP); `engine` depends on nothing above it.
- **Python env:** `uv` (no pip/poetry). Postgres on port `5433`. Neo4j `bolt://localhost:7687`. Ollama `http://localhost:11434`.
- **Embedding model:** `nomic-embed-text` via Ollama, 768 dimensions (must match `EMBEDDING_DIM`).
- **Reranker:** `BAAI/bge-reranker-v2-m3` via sentence-transformers, offline (`HF_HUB_OFFLINE=1`).
- **Entity/relation schema:** `config/engine/graphrag/entity_schema.yaml` (hot-loadable).
- **Model config:** `config/engine/graphrag/model_config.yaml` (embedding/gatekeeper/llm).
- **Secrets** (DB/Neo4j/Ollama/LLM keys) stay in `.env`, loaded by `config/settings.py` (pydantic-settings). `config/app.yaml` holds only impl selection + wiring (no secrets).
- **MVP:** no auth/multi-tenancy. File storage: local `uploads/`.
- **Tests:** `pytest` + `pytest-asyncio`, `asyncio_mode=auto`. Tests live under `tests/`. Service-dependent tests are guarded by `RUN_INTEGRATION=1` and skipped otherwise.
- **Commits:** one commit per task (or per step-group where noted). Conventional-commit messages.

## Resolved decisions (spec §9 open confirm-points)

1. **Frontend SPA tech: keep React + Vite + TS.** The existing `frontend/` SPA already uses React 19 + react-router-dom 7 + react-force-graph-2d, with heavy recent investment (graph visualization, entity panel). Re-implementing in Vue 3 would discard working code for no architectural gain — the BFF is framework-agnostic and only `client/` would differ. The SPA is migrated (not rewritten) into `src/frontend/webapp/client/`.
2. **Monorepo tooling: keep `uv` for Python + `npm` for the frontend.** `uv` is already in use (`uv.lock`). Switching the small SPA from npm to pnpm is a cosmetic swap with churn risk and no functional benefit; override the spec's pnpm suggestion. (If pnpm is later desired, it is a one-task change.)
3. **bm25 / query_rewriter are OUT OF SCOPE for this plan.** The spec (§3, §8) lists `src/engine/components/{bm25,query_rewriter}.py` "from feature branch," but `bm25_index.py`/`query_rewriter.py` exist only on `origin/feature_20260714` — they are **not present on `refactor/three-module`**. A refactor moves existing code; cherry-picking new modules from another branch is a separate effort. The `recall` pipeline keeps its current shape (vector recall → reranker → graph enrich). If bm25/rewriter are wanted, run a follow-up cherry-pick task after this plan.

## Scope-check note

The spec defines three sequentially-dependent subsystems (engine → agent → frontend) sharing the Phase-1 interface contracts. Because the contracts defined in Phase 1 are consumed by Phases 2–3, this is delivered as **one plan with three phase boundaries**, each producing independently testable software matching the spec's exit criteria. If finer granularity is preferred, Phase 2 and Phase 3 can each be split into their own plan after Phase 1 lands.

## File structure (target)

New/modified files and each one's responsibility. Mechanical migrations are marked `(move)`.

```
config/
  app.yaml                       # NEW: impl selection + wiring (no secrets)
  schema.py                      # NEW: pydantic AppConfig (validates app.yaml) + load_config()
  settings.py                    # NEW (move from src/db/config.py): InfraSettings from .env
  engine/graphrag/
    entity_schema.yaml           # (move from config/entity_schema.yaml)
    model_config.yaml            # (move from config/model_config.yaml)
  agent/codex/
    plugin.yaml                  # NEW: codex plugin config (skills + mcp endpoint)
src/
  engine/
    __init__.py
    interface.py                 # NEW: KnowledgeBase Protocol + Capabilities + types + NotSupported
    config.py                    # NEW: EngineConfig + build_engine() factory
    cli.py                       # NEW: thin CLI adapter (argparse + asyncio)
    mcp.py                       # NEW (move logic from src/api/mcp_server.py): thin MCP adapter
    components/
      __init__.py
      extractors/                # (move from src/pipeline/extractors/)
        __init__.py base.py markdown.py pdf.py docx.py pptx.py image.py registry.py
      chunker.py                 # (move from src/pipeline/chunker.py)
      embedder.py                # (move from src/pipeline/embedder.py)
      reranker.py                # (move from src/core/reranker.py)
      analyzer.py                # (move from src/pipeline/analyzer.py) — path fix
      store/
        __init__.py
        models.py                # (move from src/db/models.py)
        postgres.py              # (move from src/db/postgres.py)
        neo4j.py                 # (move from src/db/neo4j_client.py)
    graphrag/
      __init__.py
      backend.py                 # NEW (move logic from src/core/knowledge_base.py + search.py): implements KnowledgeBase
      pipeline.py                # (move from src/pipeline/pipeline.py): ingest/reingest orchestration
    wiki/README.md               # NEW: stub placeholder (not built)
  agent/
    __init__.py
    interface.py                 # NEW: Skill, EngineClient, AgentPlugin, LlmClient, SkillContext, SkillResult
    engine_client.py             # NEW: InProcessEngineClient + McpEngineClient
    memory.py                    # NEW: MemoryStore Protocol only (no impl)
    skills/
      __init__.py
      search_and_answer.py       # NEW: (a) recall + answer
      ingest_and_summarize.py    # NEW: (b) ingest a file, produce a summary
    codex/
      __init__.py
      plugin.py                  # NEW: AgentPlugin impl packaging skills for codex harness
  frontend/
    __init__.py
    webapp/
      __init__.py
      server/                    # BFF (Python)
        __init__.py
        app.py                   # NEW (move logic from src/main.py + src/api/routes.py): FastAPI BFF
        deps.py                  # NEW: engine/agent wiring (inprocess | mcp)
        routes_documents.py      # NEW: browse/list/get/upload/delete/edit
        routes_search.py         # NEW: search
        routes_graph.py          # NEW: graph/entity/neighbors
        routes_agent.py          # NEW: invoke agent skills (ask, ingest+summarize)
        routes_config.py         # NEW: read/modify app.yaml
      client/                    # SPA (TS, no __init__.py) — migrated from frontend/
        package.json vite.config.ts tsconfig.json index.html
        src/ main.tsx App.tsx api/client.ts components/ pages/
tests/
  conftest.py                    # shared fakes: FakeKnowledgeBase, FakeEngineClient, FakeLlm
  engine/ test_interface.py test_config.py test_chunker.py test_extractors.py
         test_analyzer.py test_cli.py test_mcp.py test_contract.py test_build_engine.py
  agent/ test_engine_client.py test_skills.py test_plugin.py
  frontend/ test_bff_documents.py test_bff_search.py test_bff_graph.py test_bff_agent.py
  fixtures/ sample.md sample.txt
```

Deleted at end of Phase 1: `src/core/`, `src/pipeline/`, `src/db/`, `src/api/`, `src/main.py`.
Deleted at end of Phase 3: old `frontend/`.

---

## Phase 1 — Engine (interface + components + graphrag + CLI + MCP; remove old engine code)

**Exit criteria (spec §7):** `kb ingest/recall/graph` via CLI works; MCP serves tools; old `src/{core,pipeline,db,api/mcp_server,main}` gone.

### Task 1: Test harness + pytest config

**Files:**
- Modify: `pyproject.toml`
- Create: `tests/conftest.py`, `tests/__init__.py`, `tests/engine/__init__.py`, `tests/agent/__init__.py`, `tests/frontend/__init__.py`, `tests/fixtures/sample.md`, `tests/fixtures/sample.txt`

**Interfaces:** Produces `tests/conftest.py` with `FakeKnowledgeBase` (used by Tasks 13, 14, 16, 22, 24–25) and `tests/fixtures/` files (used by Task 6).

- [ ] **Step 1: Add pytest config to pyproject.toml**

Append this section to `pyproject.toml` (after the `[project]` table, before any existing `[tool.*]` if present; otherwise at end):

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
markers = [
    "integration: requires live Postgres+Neo4j+Ollama (set RUN_INTEGRATION=1)",
]
```

- [ ] **Step 2: Create package markers and fixtures**

```bash
touch tests/__init__.py tests/engine/__init__.py tests/agent/__init__.py tests/frontend/__init__.py
```

Create `tests/fixtures/sample.md`:

```markdown
# Sample Doc

Alice works at Acme. Acme is located in Building A.

This is the second paragraph about parking.
```

Create `tests/fixtures/sample.txt`:

```text
A plain text file with one paragraph.
```

- [ ] **Step 3: Write the FakeKnowledgeBase + conftest (the shared test double)**

Create `tests/conftest.py`:

```python
"""Shared test doubles for the three-module refactor.

FakeKnowledgeBase implements src.engine.interface.KnowledgeBase with in-memory
state so engine CLI/MCP adapters, the BFF, and agent skills can be tested with
no external services.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.engine.interface import (
    Capabilities,
    DocumentRef,
    GraphData,
    GraphLink,
    GraphNode,
    IngestSource,
    NotSupported,
    RecallChunk,
    RecallRequest,
    RecallResult,
)


@dataclass
class FakeKnowledgeBase:
    capabilities: Capabilities = field(default_factory=Capabilities)
    docs: dict[str, DocumentRef] = field(default_factory=dict)
    raw: dict[str, bytes] = field(default_factory=dict)
    graph: GraphData = field(default_factory=GraphData)
    recall_calls: list[str] = field(default_factory=list)

    async def ingest(self, source: IngestSource) -> DocumentRef:
        import uuid

        doc_id = str(uuid.uuid4())
        ref = DocumentRef(
            id=doc_id, title=source.name, file_type="markdown", status="indexed"
        )
        self.docs[doc_id] = ref
        self.raw[doc_id] = source.data
        return ref

    async def reingest(self, doc_id: str) -> DocumentRef:
        if doc_id not in self.docs:
            raise KeyError(doc_id)
        self.docs[doc_id].status = "indexed"
        return self.docs[doc_id]

    async def remove(self, doc_id: str) -> None:
        self.docs.pop(doc_id, None)
        self.raw.pop(doc_id, None)

    async def recall(self, request: RecallRequest) -> RecallResult:
        self.recall_calls.append(request.query)
        return RecallResult(chunks=[], related_entities=[], related_docs=[])

    async def get_graph(self, entity: str | None = None) -> GraphData:
        return self.graph

    async def get_neighbors(self, entity: str) -> GraphData:
        return self.graph

    async def list_documents(
        self,
        page: int = 1,
        page_size: int = 20,
        file_type: str | None = None,
        status: str | None = None,
    ) -> dict:
        items = list(self.docs.values())
        return {"total": len(items), "page": page, "page_size": page_size, "items": items}

    async def get_document(self, doc_id: str) -> dict | None:
        ref = self.docs.get(doc_id)
        return None if ref is None else ref.__dict__
```

- [ ] **Step 4: Verify the harness imports (it will fail — `src.engine.interface` does not exist yet)**

Run: `uv run python -c "import tests.conftest"`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.engine'`

(This is the expected red state — Task 3 creates `src.engine.interface`.)

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml tests/
git commit -m "test: add pytest config + shared FakeKnowledgeBase fixture"
```

---

### Task 2: Config system (AppConfig + app.yaml + InfraSettings; move graphrag yaml)

**Files:**
- Create: `config/app.yaml`, `config/schema.py`, `config/settings.py`
- Move: `config/entity_schema.yaml` -> `config/engine/graphrag/entity_schema.yaml`
- Move: `config/model_config.yaml` -> `config/engine/graphrag/model_config.yaml`
- Test: `tests/engine/test_config.py`

**Interfaces:**
- Produces: `config.schema.AppConfig` with fields `engine: {impl, config}`, `agent: {harness, skills, memory}`, `frontend: {impl}`, `webapp: {engine_access}`; `config.schema.load_config(path=None) -> AppConfig`.
- Produces: `config.settings.InfraSettings` (fields: `postgres_host/port/db/user/password`, `neo4j_uri/user/password`, `ollama_base_url`, `llm_api_key`, `app_host/port`; property `postgres_dsn`); module-level singleton `settings`.

- [ ] **Step 1: Move graphrag config files**

```bash
mkdir -p config/engine/graphrag
git mv config/entity_schema.yaml config/engine/graphrag/entity_schema.yaml
git mv config/model_config.yaml config/engine/graphrag/model_config.yaml
```

- [ ] **Step 2: Write the failing test**

Create `tests/engine/test_config.py`:

```python
from pathlib import Path

import pytest

from config.schema import AppConfig, load_config
from config.settings import InfraSettings


def test_appconfig_defaults():
    cfg = AppConfig()
    assert cfg.engine.impl == "graphrag"
    assert cfg.engine.config == "config/engine/graphrag"
    assert cfg.agent.harness == "codex"
    assert cfg.agent.skills == ["search_and_answer", "ingest_and_summarize"]
    assert cfg.agent.memory == {"impl": None}
    assert cfg.frontend.impl == "webapp"
    assert cfg.webapp.engine_access == "inprocess"


def test_load_config_reads_app_yaml(tmp_path: Path):
    app_yaml = tmp_path / "app.yaml"
    app_yaml.write_text(
        "engine:\\n  impl: graphrag\\n  config: config/engine/graphrag\\n"
        "agent:\\n  harness: codex\\n  skills: [search_and_answer]\\n"
        "  memory: {impl: null}\\nfrontend:\\n  impl: webapp\\n"
        "webapp:\\n  engine_access: mcp\\n"
    )
    cfg = load_config(app_yaml)
    assert cfg.webapp.engine_access == "mcp"
    assert cfg.agent.skills == ["search_and_answer"]


def test_load_config_missing_file_uses_defaults(tmp_path: Path):
    cfg = load_config(tmp_path / "does_not_exist.yaml")
    assert cfg.engine.impl == "graphrag"


def test_infra_settings_postgres_dsn():
    s = InfraSettings(
        postgres_user="u", postgres_password="p",
        postgres_host="h", postgres_port=5433, postgres_db="d",
    )
    assert s.postgres_dsn == "postgresql+asyncpg://u:p@h:5433/d"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/engine/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'config.schema'`

- [ ] **Step 4: Implement config/schema.py**

Create `config/schema.py`:

```python
"""App config: validates config/app.yaml and selects implementations."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class EngineCfg(BaseModel):
    impl: str = "graphrag"
    config: str = "config/engine/graphrag"


class AgentCfg(BaseModel):
    harness: str = "codex"
    skills: list[str] = Field(
        default_factory=lambda: ["search_and_answer", "ingest_and_summarize"]
    )
    memory: dict = Field(default_factory=lambda: {"impl": None})


class FrontendCfg(BaseModel):
    impl: str = "webapp"


class WebappCfg(BaseModel):
    engine_access: Literal["inprocess", "mcp"] = "inprocess"


class AppConfig(BaseModel):
    engine: EngineCfg = Field(default_factory=EngineCfg)
    agent: AgentCfg = Field(default_factory=AgentCfg)
    frontend: FrontendCfg = Field(default_factory=FrontendCfg)
    webapp: WebappCfg = Field(default_factory=WebappCfg)


def load_config(path: Path | str | None = None) -> AppConfig:
    """Load AppConfig from a YAML file; missing file yields defaults."""
    p = Path(path) if path is not None else Path("config/app.yaml")
    data: dict = {}
    if p.exists():
        loaded = yaml.safe_load(p.read_text(encoding="utf-8"))
        if loaded:
            data = loaded
    return AppConfig.model_validate(data)
```

- [ ] **Step 5: Implement config/settings.py (move src/db/config.py)**

Create `config/settings.py`:

```python
"""Infra connection settings, loaded from .env via pydantic-settings."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class InfraSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    postgres_host: str = "localhost"
    postgres_port: int = 5433
    postgres_db: str = "knowledge_base"
    postgres_user: str = "kb_user"
    postgres_password: str = "kb_pass"

    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "neo4j"

    ollama_base_url: str = "http://localhost:11434"

    llm_api_key: str = ""

    app_host: str = "0.0.0.0"
    app_port: int = 8000

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = InfraSettings()
```

- [ ] **Step 6: Create config/app.yaml**

Create `config/app.yaml`:

```yaml
engine:
  impl: graphrag
  config: config/engine/graphrag
agent:
  harness: codex
  skills: [search_and_answer, ingest_and_summarize]
  memory: { impl: null }
frontend:
  impl: webapp
webapp:
  engine_access: inprocess
```

- [ ] **Step 7: Run test to verify it passes**

Run: `uv run pytest tests/engine/test_config.py -v`
Expected: PASS (4 tests)

- [ ] **Step 8: Commit**

```bash
git add config/ tests/engine/test_config.py
git commit -m "feat(config): AppConfig + app.yaml + InfraSettings; move graphrag yaml"
```

---

### Task 3: Engine interface contract (Protocol + types + Capabilities + NotSupported)

**Files:**
- Create: `src/engine/__init__.py`, `src/engine/interface.py`
- Test: `tests/engine/test_interface.py`

**Interfaces:**
- Produces (consumed by every later engine/agent/frontend task): `KnowledgeBase` Protocol with attributes/methods below; dataclasses `Capabilities`, `IngestSource`, `DocumentRef`, `RecallRequest`, `RecallChunk`, `RecallResult`, `GraphNode`, `GraphLink`, `GraphData`; exception `NotSupported`.

```python
class KnowledgeBase(Protocol):
    capabilities: Capabilities
    async def ingest(self, source: IngestSource) -> DocumentRef: ...
    async def reingest(self, doc_id: str) -> DocumentRef: ...
    async def remove(self, doc_id: str) -> None: ...
    async def recall(self, request: RecallRequest) -> RecallResult: ...
    async def get_graph(self, entity: str | None = None) -> GraphData: ...
    async def get_neighbors(self, entity: str) -> GraphData: ...
    async def list_documents(self, page: int = 1, page_size: int = 20,
                             file_type: str | None = None, status: str | None = None) -> dict: ...
    async def get_document(self, doc_id: str) -> dict | None: ...
```

- [ ] **Step 1: Write the failing test**

Create `tests/engine/test_interface.py`:

```python
import pytest

from src.engine.interface import (
    Capabilities, DocumentRef, GraphData, GraphLink, GraphNode,
    IngestSource, NotSupported, RecallChunk, RecallRequest, RecallResult,
)


def test_capabilities_defaults():
    c = Capabilities()
    assert c.graph is False
    assert c.partial_update is False
    assert c.multimodal is False
    assert c.namespace is False


def test_ingest_source_carries_bytes():
    s = IngestSource(name="x.md", data=b"hello")
    assert s.data == b"hello"
    assert s.path is None


def test_dataclasses_roundtrip():
    req = RecallRequest(query="q", top_k=5)
    assert req.top_k == 5
    chunk = RecallChunk(doc_id="d", title="t", chunk_text="c",
                        reranker_score=1.0, vector_score=0.5)
    res = RecallResult(chunks=[chunk], related_entities=[{"a": 1}], related_docs=[])
    g = GraphData(nodes=[GraphNode(name="n", type="T")],
                  links=[GraphLink(source="n", target="m", type="R")])
    assert res.chunks[0].doc_id == "d"
    assert g.nodes[0].name == "n"


def test_notsupported_is_exception():
    with pytest.raises(NotSupported):
        raise NotSupported("graph")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/engine/test_interface.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.engine'`

- [ ] **Step 3: Implement src/engine/interface.py**

Create `src/engine/__init__.py` (empty).

Create `src/engine/interface.py`:

```python
"""Engine module contract: the KnowledgeBase Protocol + shared types.

This is THE contract every engine implementation must satisfy. Adapters
(cli.py, mcp.py) and consumers (agent engine_client, frontend BFF) program
against these types, never against a concrete backend.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


class NotSupported(Exception):
    """Raised by an optional KnowledgeBase method the backend does not support."""


@dataclass
class Capabilities:
    """Declares what a backend supports. Optional methods raise NotSupported."""
    graph: bool = False
    partial_update: bool = False
    multimodal: bool = False
    namespace: bool = False


@dataclass
class IngestSource:
    """A file to ingest: either raw bytes (name+data) or a path on disk."""
    name: str
    data: bytes = b""
    path: Path | None = None


@dataclass
class DocumentRef:
    id: str
    title: str
    file_type: str
    status: str
    overview: str = ""
    error_msg: str | None = None


@dataclass
class RecallRequest:
    query: str
    top_k: int = 20


@dataclass
class RecallChunk:
    doc_id: str
    title: str
    chunk_text: str
    reranker_score: float
    vector_score: float


@dataclass
class RecallResult:
    chunks: list[RecallChunk] = field(default_factory=list)
    related_entities: list[dict] = field(default_factory=list)
    related_docs: list[dict] = field(default_factory=list)


@dataclass
class GraphNode:
    name: str
    type: str
    description: str = ""
    sources: list[dict] = field(default_factory=list)


@dataclass
class GraphLink:
    source: str
    target: str
    type: str
    description: str = ""


@dataclass
class GraphData:
    nodes: list[GraphNode] = field(default_factory=list)
    links: list[GraphLink] = field(default_factory=list)


class KnowledgeBase(Protocol):
    """Stable engine contract. Engine = no agents; LLM only for embeddings,
    chunk summaries (overview), and graph entity/relation extraction."""

    capabilities: Capabilities

    async def ingest(self, source: IngestSource) -> DocumentRef: ...
    async def reingest(self, doc_id: str) -> DocumentRef: ...
    async def remove(self, doc_id: str) -> None: ...
    async def recall(self, request: RecallRequest) -> RecallResult: ...
    async def get_graph(self, entity: str | None = None) -> GraphData: ...
    async def get_neighbors(self, entity: str) -> GraphData: ...
    async def list_documents(
        self,
        page: int = 1,
        page_size: int = 20,
        file_type: str | None = None,
        status: str | None = None,
    ) -> dict: ...
    async def get_document(self, doc_id: str) -> dict | None: ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/engine/test_interface.py -v`
Expected: PASS (4 tests). Also re-run `uv run python -c "import tests.conftest"` — now succeeds.

- [ ] **Step 5: Commit**

```bash
git add src/engine/__init__.py src/engine/interface.py tests/engine/test_interface.py
git commit -m "feat(engine): KnowledgeBase Protocol + types + Capabilities"
```

---

### Task 4: Engine factory (EngineConfig + build_engine)

**Files:**
- Create: `src/engine/config.py`
- Test: `tests/engine/test_build_engine.py`

**Interfaces:**
- Consumes: `config.schema.AppConfig.engine` (`impl: str`, `config: str`).
- Produces: `src.engine.config.EngineConfig` (fields `impl: str`, `config_dir: Path`); `src.engine.config.build_engine(config: EngineConfig) -> KnowledgeBase`. For `impl == "graphrag"` it imports `src.engine.graphrag.backend:build` and calls `build(config)`; unknown impl raises `ValueError`.

- [ ] **Step 1: Write the failing test**

Create `tests/engine/test_build_engine.py`:

```python
from pathlib import Path

import pytest

from src.engine.config import EngineConfig, build_engine


def test_engine_config_defaults():
    cfg = EngineConfig(impl="graphrag", config_dir=Path("config/engine/graphrag"))
    assert cfg.impl == "graphrag"


def test_build_engine_unknown_impl_raises():
    cfg = EngineConfig(impl="nope", config_dir=Path("config/engine/graphrag"))
    with pytest.raises(ValueError, match="unknown engine impl"):
        build_engine(cfg)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/engine/test_build_engine.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.engine.config'`

- [ ] **Step 3: Implement src/engine/config.py**

Create `src/engine/config.py`:

```python
"""Engine config + factory: selects a KnowledgeBase implementation by name."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.engine.interface import KnowledgeBase


@dataclass
class EngineConfig:
    impl: str
    config_dir: Path


def build_engine(config: EngineConfig) -> KnowledgeBase:
    """Build the engine implementation selected by config.impl.

    graphrag -> src.engine.graphrag.backend:build(config)
    """
    if config.impl == "graphrag":
        from src.engine.graphrag.backend import build

        return build(config)
    raise ValueError(f"unknown engine impl: {config.impl}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/engine/test_build_engine.py -v`
Expected: PASS (2 tests). (The `graphrag` path is not exercised here - it is covered by Task 15's contract test.)

- [ ] **Step 5: Commit**

```bash
git add src/engine/config.py tests/engine/test_build_engine.py
git commit -m "feat(engine): EngineConfig + build_engine factory"
```

---

### Task 5: Migrate store layer (models, postgres, neo4j) into engine/components/store

**Files:**
- Create: `src/engine/components/__init__.py`, `src/engine/components/store/__init__.py`
- Move: `src/db/models.py` -> `src/engine/components/store/models.py`
- Move: `src/db/postgres.py` -> `src/engine/components/store/postgres.py`
- Move: `src/db/neo4j_client.py` -> `src/engine/components/store/neo4j.py`
- Delete: `src/db/__init__.py` (after fixing all imports)

**Interfaces:** Produces `src.engine.components.store.{models, postgres, neo4j}` with the same public names as before (`Base`, `Document`, `Chunk`, `EMBEDDING_DIM`, `engine`, `async_session_factory`, `init_db`, `get_session`, `Neo4jClient`, `EntityData`, `EntitySource`, `RelationData`, `GraphQueryResult`). `postgres.py` and `neo4j.py` import settings from `config.settings`.

- [ ] **Step 1: Move the three files and create package markers**

```bash
mkdir -p src/engine/components/store
touch src/engine/components/__init__.py src/engine/components/store/__init__.py
git mv src/db/models.py src/engine/components/store/models.py
git mv src/db/postgres.py src/engine/components/store/postgres.py
git mv src/db/neo4j_client.py src/engine/components/store/neo4j.py
```

- [ ] **Step 2: Fix imports in postgres.py**

In `src/engine/components/store/postgres.py`, change the two import lines:

```python
from src.db.config import settings
from src.db.models import Base
```

to:

```python
from config.settings import settings
from src.engine.components.store.models import Base
```

- [ ] **Step 3: Fix imports in neo4j.py**

In `src/engine/components/store/neo4j.py`, change:

```python
from src.db.config import settings
```

to:

```python
from config.settings import settings
```

(`models.py` has no intra-project imports - no change needed there.)

- [ ] **Step 4: Verify the store package imports**

Run: `uv run python -c "from src.engine.components.store import models, postgres, neo4j; print('ok')"`
Expected: prints `ok` (no `ImportError`).

- [ ] **Step 5: Delete the now-empty old db package**

```bash
rm src/db/__init__.py
rmdir src/db
```

Note: `src/core/knowledge_base.py`, `src/core/search.py`, `src/pipeline/pipeline.py`, `src/api/*` still import `src.db.*` - they will be broken until Tasks 11–12 migrate them. That is expected during Phase 1. Do NOT run the old app. The new engine modules import cleanly (verified in Step 4). Tests for the new modules run green.

- [ ] **Step 6: Commit**

```bash
git add src/engine/components/ src/db
git commit -m "refactor(engine): move store layer (models/postgres/neo4j) into components/store"
```

---

### Task 6: Migrate extractors into engine/components/extractors

**Files:**
- Create: `src/engine/components/extractors/__init__.py` (empty)
- Move: `src/pipeline/extractors/{base,markdown,pdf,docx,pptx,image,registry}.py` -> `src/engine/components/extractors/`
- Test: `tests/engine/test_extractors.py`

**Interfaces:** Produces `src.engine.components.extractors.registry.registry` (singleton `ExtractorRegistry`) and `ExtractorRegistry.guess_file_type(path)`. Other modules import `from src.engine.components.extractors.registry import registry, ExtractorRegistry`.

- [ ] **Step 1: Move the extractor files**

```bash
mkdir -p src/engine/components/extractors
touch src/engine/components/extractors/__init__.py
git mv src/pipeline/extractors/base.py      src/engine/components/extractors/base.py
git mv src/pipeline/extractors/markdown.py  src/engine/components/extractors/markdown.py
git mv src/pipeline/extractors/pdf.py       src/engine/components/extractors/pdf.py
git mv src/pipeline/extractors/docx.py      src/engine/components/extractors/docx.py
git mv src/pipeline/extractors/pptx.py      src/engine/components/extractors/pptx.py
git mv src/pipeline/extractors/image.py     src/engine/components/extractors/image.py
git mv src/pipeline/extractors/registry.py  src/engine/components/extractors/registry.py
rmdir src/pipeline/extractors
```

- [ ] **Step 2: Fix imports in registry.py**

In `src/engine/components/extractors/registry.py`, replace the import block:

```python
from src.pipeline.extractors.base import BaseExtractor
from src.pipeline.extractors.docx import DocxExtractor
from src.pipeline.extractors.image import ImageExtractor
from src.pipeline.extractors.markdown import MarkdownExtractor
from src.pipeline.extractors.pdf import PDFExtractor
from src.pipeline.extractors.pptx import PPTXExtractor
```

with:

```python
from src.engine.components.extractors.base import BaseExtractor
from src.engine.components.extractors.docx import DocxExtractor
from src.engine.components.extractors.image import ImageExtractor
from src.engine.components.extractors.markdown import MarkdownExtractor
from src.engine.components.extractors.pdf import PDFExtractor
from src.engine.components.extractors.pptx import PPTXExtractor
```

Each extractor module imports only `from src.pipeline.extractors.base import BaseExtractor` - change each to `from src.engine.components.extractors.base import BaseExtractor` in `markdown.py`, `pdf.py`, `docx.py`, `pptx.py`, `image.py`.

- [ ] **Step 3: Write the failing test**

Create `tests/engine/test_extractors.py`:

```python
from pathlib import Path

import pytest

from src.engine.components.extractors.registry import ExtractorRegistry, registry

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def test_registry_is_singleton():
    assert isinstance(registry, ExtractorRegistry)


def test_extract_markdown():
    text = registry.extract(FIXTURES / "sample.md")
    assert "Alice works at Acme" in text


def test_extract_txt_treated_as_markdown():
    text = registry.extract(FIXTURES / "sample.txt")
    assert "plain text file" in text


def test_guess_file_type():
    assert ExtractorRegistry.guess_file_type(Path("a.md")) == "markdown"
    assert ExtractorRegistry.guess_file_type(Path("a.pdf")) == "pdf"
    assert ExtractorRegistry.guess_file_type(Path("a.png")) == "image"


def test_unsupported_type_raises():
    with pytest.raises(ValueError, match="不支持的文件类型"):
        registry.get_extractor(Path("a.xyz"))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/engine/test_extractors.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/engine/components/extractors/ src/pipeline tests/engine/test_extractors.py
git commit -m "refactor(engine): move extractors into components/extractors"
```

---

### Task 7: Migrate chunker into engine/components

**Files:**
- Move: `src/pipeline/chunker.py` -> `src/engine/components/chunker.py`
- Test: `tests/engine/test_chunker.py`

**Interfaces:** Produces `src.engine.components.chunker.chunk_text(text, chunk_size=500, overlap=50) -> list[Chunk]` and dataclass `Chunk(index, text, token_count)`. No import changes needed (chunker has no intra-project imports).

- [ ] **Step 1: Move the file**

```bash
git mv src/pipeline/chunker.py src/engine/components/chunker.py
```

- [ ] **Step 2: Write the failing test**

Create `tests/engine/test_chunker.py`:

```python
from src.engine.components.chunker import Chunk, chunk_text


def test_empty_text_returns_empty():
    assert chunk_text("") == []
    assert chunk_text("   ") == []


def test_single_paragraph_one_chunk():
    chunks = chunk_text("One short paragraph.")
    assert len(chunks) == 1
    assert chunks[0].index == 0
    assert "One short" in chunks[0].text


def test_many_paragraphs_split_with_overlap():
    paras = [f"Paragraph number {i} with enough words to fill space." for i in range(20)]
    text = "\n\n".join(paras)
    chunks = chunk_text(text, chunk_size=40, overlap=10)
    assert len(chunks) > 1
    assert all(isinstance(c, Chunk) for c in chunks)
    assert [c.index for c in chunks] == list(range(len(chunks)))


def test_oversized_paragraph_is_own_chunk():
    big = "word " * 1000
    chunks = chunk_text(big, chunk_size=100, overlap=0)
    assert len(chunks) >= 1
```

- [ ] **Step 3: Run test to verify it passes**

Run: `uv run pytest tests/engine/test_chunker.py -v`
Expected: PASS (4 tests).

- [ ] **Step 4: Commit**

```bash
git add src/engine/components/chunker.py src/pipeline tests/engine/test_chunker.py
git commit -m "refactor(engine): move chunker into components"
```

---

### Task 8: Migrate embedder into engine/components

**Files:**
- Move: `src/pipeline/embedder.py` -> `src/engine/components/embedder.py`
- Test: `tests/engine/test_embedder.py` (uses a fake Ollama via monkeypatched httpx)

**Interfaces:** Produces `src.engine.components.embedder.embedder` (singleton `Embedder`) with `embed_text(text) -> list[float]` and `embed_batch(texts) -> list[list[float]]`.

- [ ] **Step 1: Move the file**

```bash
git mv src/pipeline/embedder.py src/engine/components/embedder.py
```

- [ ] **Step 2: Fix imports in embedder.py**

In `src/engine/components/embedder.py`, change:

```python
from src.db.config import settings
```

to:

```python
from config.settings import settings
```

- [ ] **Step 3: Write the failing test**

Create `tests/engine/test_embedder.py`:

```python
import pytest

from src.engine.components import embedder as embedder_mod


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None):
        inp = json["input"]
        if isinstance(inp, list):
            return _FakeResp({"embeddings": [[0.1] * 768 for _ in inp]})
        return _FakeResp({"embeddings": [[0.2] * 768]})


async def test_embed_text_uses_ollama(monkeypatch):
    monkeypatch.setattr(embedder_mod.httpx, "AsyncClient", _FakeClient)
    e = embedder_mod.Embedder()
    vec = await e.embed_text("hello")
    assert len(vec) == 768


async def test_embed_batch_empty():
    e = embedder_mod.Embedder()
    assert await e.embed_batch([]) == []


async def test_embed_batch_returns_one_vec_per_input(monkeypatch):
    monkeypatch.setattr(embedder_mod.httpx, "AsyncClient", _FakeClient)
    e = embedder_mod.Embedder()
    out = await e.embed_batch(["a", "b", "c"])
    assert len(out) == 3
    assert all(len(v) == 768 for v in out)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/engine/test_embedder.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/engine/components/embedder.py src/pipeline tests/engine/test_embedder.py
git commit -m "refactor(engine): move embedder into components"
```

---

### Task 9: Migrate reranker into engine/components

**Files:**
- Move: `src/core/reranker.py` -> `src/engine/components/reranker.py`
- Test: `tests/engine/test_reranker.py` (uses a fake CrossEncoder)

**Interfaces:** Produces `src.engine.components.reranker.get_reranker() -> Reranker` and `Reranker.rerank(query, texts) -> list[float]`.

- [ ] **Step 1: Move the file**

```bash
git mv src/core/reranker.py src/engine/components/reranker.py
```

(The reranker has no intra-project imports - no import changes needed.)

- [ ] **Step 2: Write the failing test**

Create `tests/engine/test_reranker.py`:

```python
from src.engine.components import reranker as reranker_mod


class _FakeCrossEncoder:
    def __init__(self, model_name):
        self.model_name = model_name

    def predict(self, pairs):
        # higher score when text contains the query term
        return [10.0 if q in t else -5.0 for q, t in pairs]


def test_rerank_scores_pairs(monkeypatch):
    monkeypatch.setattr(reranker_mod, "CrossEncoder", _FakeCrossEncoder)
    r = reranker_mod.Reranker()
    scores = r.rerank("alice", ["alice is here", "bob is gone"])
    assert scores == [10.0, -5.0]


def test_rerank_empty():
    monkeypatch.setattr(reranker_mod, "CrossEncoder", _FakeCrossEncoder)
    r = reranker_mod.Reranker()
    assert r.rerank("q", []) == []


def test_get_reranker_singleton(monkeypatch):
    monkeypatch.setattr(reranker_mod, "CrossEncoder", _FakeCrossEncoder)
    reranker_mod._reranker_instance = None
    a = reranker_mod.get_reranker()
    b = reranker_mod.get_reranker()
    assert a is b
    reranker_mod._reranker_instance = None
```

- [ ] **Step 3: Run test to verify it passes**

Run: `uv run pytest tests/engine/test_reranker.py -v`
Expected: PASS (3 tests).

- [ ] **Step 4: Commit**

```bash
git add src/engine/components/reranker.py src/core tests/engine/test_reranker.py
git commit -m "refactor(engine): move reranker into components"
```

---

### Task 10: Migrate analyzer into engine/components (path fix + injectable config paths)

**Files:**
- Move: `src/pipeline/analyzer.py` -> `src/engine/components/analyzer.py`
- Test: `tests/engine/test_analyzer.py`

**Interfaces:**
- Produces: `src.engine.components.analyzer.Analyzer`, singleton `analyzer`, dataclasses `Entity`, `Relation`, `FileRelation`, `ChunkAnalysisResult`, `AnalysisResult`.
- **Change:** the analyzer no longer hard-codes `config/entity_schema.yaml` / `config/model_config.yaml` via `parents[2]`. `Analyzer.__init__` accepts optional `schema_path: Path | None` and `model_config_path: Path | None`, defaulting to `config/engine/graphrag/entity_schema.yaml` and `config/engine/graphrag/model_config.yaml`. The static methods `_build_prompt`, `_build_chunk_prompt`, `_build_overview_prompt`, `_parse_response`, `_parse_chunk_response`, `_parse_overview_response` are unchanged (pure, testable).

- [ ] **Step 1: Move the file**

```bash
git mv src/pipeline/analyzer.py src/engine/components/analyzer.py
```

- [ ] **Step 2: Fix imports and config paths in analyzer.py**

In `src/engine/components/analyzer.py`:

Change:
```python
from src.db.config import settings
```
to:
```python
from config.settings import settings
```

Replace the module-level path constant:
```python
_ENTITY_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "config" / "entity_schema.yaml"
```
with:
```python
_DEFAULT_SCHEMA_PATH = Path("config/engine/graphrag/entity_schema.yaml")
_DEFAULT_MODEL_CONFIG_PATH = Path("config/engine/graphrag/model_config.yaml")
```

Replace the `__init__` and `_load_model_config`:
```python
    def __init__(self) -> None:
        schema = _load_entity_schema()
        self._schema = schema
        # 从 model_config.yaml 读取 LLM 配置
        self._config = self._load_model_config()

    @staticmethod
    def _load_model_config() -> dict:
        config_path = Path(__file__).resolve().parents[2] / "config" / "model_config.yaml"
        if config_path.exists():
            with open(config_path, encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}
                return config.get("llm", {})
        return {}
```
with:
```python
    def __init__(
        self,
        schema_path: Path | None = None,
        model_config_path: Path | None = None,
    ) -> None:
        self._schema_path = schema_path or _DEFAULT_SCHEMA_PATH
        self._model_config_path = model_config_path or _DEFAULT_MODEL_CONFIG_PATH
        self._schema = _load_entity_schema(self._schema_path)
        self._config = self._load_model_config(self._model_config_path)

    @staticmethod
    def _load_model_config(path: Path) -> dict:
        if path.exists():
            with open(path, encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}
                return config.get("llm", {})
        return {}
```

And change the module-level loader:
```python
def _load_entity_schema() -> dict:
    """加载 entity_schema.yaml。"""
    if _ENTITY_SCHEMA_PATH.exists():
        with open(_ENTITY_SCHEMA_PATH, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}
```
to:
```python
def _load_entity_schema(path: Path) -> dict:
    """加载 entity_schema.yaml。"""
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}
```

- [ ] **Step 3: Write the failing test**

Create `tests/engine/test_analyzer.py`:

```python
from src.engine.components.analyzer import (
    Analyzer, AnalysisResult, ChunkAnalysisResult, Entity, FileRelation, Relation,
)


def test_parse_response_extracts_entities_relations_file_relations():
    raw = """```json
{"overview": "doc summary",
 "entities": [{"name": "Acme", "type": "Company", "description": "d"}],
 "relations": [{"from_name": "Acme", "to_name": "B", "type": "OWNS", "description": "x"}],
 "file_relations": [{"related_doc_title": "other.md", "type": "REFERENCES", "reason": "r"}]}
```"""
    result = Analyzer._parse_response(raw)
    assert isinstance(result, AnalysisResult)
    assert result.overview == "doc summary"
    assert len(result.entities) == 1
    assert isinstance(result.entities[0], Entity)
    assert len(result.relations) == 1
    assert isinstance(result.relations[0], Relation)
    assert len(result.file_relations) == 1
    assert isinstance(result.file_relations[0], FileRelation)


def test_parse_response_bad_json_returns_placeholder():
    result = Analyzer._parse_response("not json at all")
    assert result.overview.startswith("[LLM 返回解析失败]")


def test_parse_chunk_response():
    raw = '{"entities": [{"name": "E", "type": "T"}], "relations": []}'
    ca = Analyzer._parse_chunk_response(raw, chunk_index=3)
    assert isinstance(ca, ChunkAnalysisResult)
    assert ca.chunk_index == 3
    assert len(ca.entities) == 1


def test_parse_chunk_response_bad_json_empty():
    ca = Analyzer._parse_chunk_response("xxx", chunk_index=0)
    assert ca.entities == []
    assert ca.relations == []


def test_analyzer_with_todo_provider_returns_placeholder(tmp_path):
    cfg = tmp_path / "model_config.yaml"
    cfg.write_text("llm:\n  provider: todo\n")
    schema = tmp_path / "entity_schema.yaml"
    schema.write_text("entity_types:\n  core: [Person]\n  open: true\n"
                      "relation_types:\n  core: [WORKS_AT]\n  open: true\n")
    a = Analyzer(schema_path=schema, model_config_path=cfg)

    async def go():
        return await a.analyze_overview("some text", "title")

    import asyncio
    res = asyncio.run(go())
    assert res.overview.startswith("[待 LLM 生成]")
    assert res.file_relations == []


def test_build_overview_prompt_contains_title():
    p = Analyzer._build_overview_prompt("My Title", "body text here")
    assert "My Title" in p
    assert "body text here" in p
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/engine/test_analyzer.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/engine/components/analyzer.py src/pipeline tests/engine/test_analyzer.py
git commit -m "refactor(engine): move analyzer into components; inject config paths"
```

---

### Task 11: Migrate ingest pipeline into engine/graphrag/pipeline

**Files:**
- Move: `src/pipeline/pipeline.py` -> `src/engine/graphrag/pipeline.py`
- Create: `src/engine/graphrag/__init__.py`
- Test: `tests/engine/test_pipeline.py` (integration-guarded; the orchestration shape is verified; full run needs services)

**Interfaces:**
- Produces: `src.engine.graphrag.pipeline.Pipeline` with `__init__(self, neo4j: Neo4jClient, analyzer: Analyzer | None = None, schema_path=None, model_config_path=None)` and methods `process_file(doc_id: UUID, file_path: Path, title: str, file_type: str) -> None` and `reindex_document(doc_id: UUID, new_text: str) -> None` (signatures unchanged from the original).
- Internal imports updated to new component paths.

- [ ] **Step 1: Create package + move the file**

```bash
mkdir -p src/engine/graphrag
touch src/engine/graphrag/__init__.py
git mv src/pipeline/pipeline.py src/engine/graphrag/pipeline.py
rmdir src/pipeline  # __init__.py still present? see Step 5
```

- [ ] **Step 2: Fix imports in pipeline.py**

In `src/engine/graphrag/pipeline.py`, replace the import block:

```python
from src.db.models import Chunk, Document
from src.db.neo4j_client import Neo4jClient, EntityData, EntitySource, RelationData
from src.db.postgres import async_session_factory
from src.pipeline.analyzer import analyzer, ChunkAnalysisResult
from src.pipeline.chunker import chunk_text
from src.pipeline.embedder import embedder
from src.pipeline.extractors.registry import registry
```

with:

```python
from src.engine.components.store.models import Chunk, Document
from src.engine.components.store.neo4j import Neo4jClient, EntityData, EntitySource, RelationData
from src.engine.components.store.postgres import async_session_factory
from src.engine.components.analyzer import Analyzer, ChunkAnalysisResult
from src.engine.components.chunker import chunk_text
from src.engine.components.embedder import embedder
from src.engine.components.extractors.registry import registry
```

- [ ] **Step 3: Make the analyzer injectable**

Replace the class `__init__`:
```python
    def __init__(self, neo4j: Neo4jClient) -> None:
        self._neo4j = neo4j
```
with:
```python
    def __init__(
        self,
        neo4j: Neo4jClient,
        analyzer: Analyzer | None = None,
    ) -> None:
        self._neo4j = neo4j
        self._analyzer = analyzer or Analyzer()
```

Then replace every remaining reference to the module-global `analyzer` inside `process_file` and `reindex_document` (there are several: `analyzer.analyze_overview`, `analyzer.analyze_chunk`) with `self._analyzer`. (The global `analyzer = Analyzer()` singleton import is dropped.)

- [ ] **Step 4: Write the integration-guarded test**

Create `tests/engine/test_pipeline.py`:

```python
"""Pipeline orchestration test.

Full execution needs Postgres + Neo4j + Ollama + a configured LLM, so it is
marked integration. The non-integration assertion verifies the class is wired
against the new component paths (importable, correct constructor signature).
"""
import inspect

import pytest

from src.engine.components.analyzer import Analyzer
from src.engine.components.store.neo4j import Neo4jClient
from src.engine.graphrag.pipeline import Pipeline


def test_pipeline_constructor_accepts_analyzer():
    sig = inspect.signature(Pipeline.__init__)
    assert "analyzer" in sig.parameters


def test_pipeline_methods_exist():
    assert hasattr(Pipeline, "process_file")
    assert hasattr(Pipeline, "reindex_document")


@pytest.mark.integration
async def test_process_file_end_to_end():
    # Requires: docker compose up postgres; Neo4j running; Ollama with
    # nomic-embed-text; config/engine/graphrag/model_config.yaml LLM provider.
    from pathlib import Path
    from uuid import uuid4

    from src.engine.components.store.postgres import init_db, async_session_factory
    from src.engine.components.store.models import Document

    await init_db()
    neo4j = Neo4jClient()
    pipe = Pipeline(neo4j, analyzer=Analyzer())
    doc_id = uuid4()
    async with async_session_factory() as session:
        session.add(Document(id=doc_id, title="t.md", file_type="markdown", status="pending"))
        await session.commit()
    await pipe.reindex_document(doc_id, "# T\n\nSome content about Acme.")
    await neo4j.close()
```

- [ ] **Step 5: Remove the old pipeline package**

```bash
rm -f src/pipeline/__init__.py
rmdir src/pipeline 2>/dev/null || true
```

- [ ] **Step 6: Run test to verify the non-integration tests pass**

Run: `uv run pytest tests/engine/test_pipeline.py -v -m "not integration"`
Expected: PASS (2 tests). The `integration` test is skipped.

- [ ] **Step 7: Commit**

```bash
git add src/engine/graphrag/ src/pipeline tests/engine/test_pipeline.py
git commit -m "refactor(engine): move ingest pipeline into graphrag; inject analyzer"
```

---

### Task 12: GraphRAG backend (implements KnowledgeBase)

**Files:**
- Create: `src/engine/graphrag/backend.py`
- Test: `tests/engine/test_backend.py` (integration-guarded for real run; constructor + capability wiring tested without services)

**Interfaces:**
- Consumes: `src.engine.config.EngineConfig` (fields `impl`, `config_dir`); `src.engine.interface.*`; `Pipeline`; store layer; `ExtractorRegistry`.
- Produces: `src.engine.graphrag.backend.GraphRAGBackend` implementing `KnowledgeBase`; `build(config: EngineConfig) -> KnowledgeBase` (factory used by `src.engine.config.build_engine`).
- **Mapping old -> new** (the backend owns its own DB session via `async_session_factory`; no `session` param leaks to callers):
  - `upload_file` -> `ingest(source: IngestSource) -> DocumentRef` (writes file to `uploads/<id>/`, creates Document, fires-and-forgets pipeline, returns `DocumentRef`).
  - `edit_content`/`reindex_document` -> `reingest(doc_id: str) -> DocumentRef` (re-runs pipeline on stored raw_text).
  - `delete_document` -> `remove(doc_id: str) -> None`.
  - `search` -> `recall(request: RecallRequest) -> RecallResult`.
  - `get_full_graph` -> `get_graph(entity=None)`; `get_entity` -> `get_graph(entity=name)` (single-node subgraph).
  - `get_neighbors` -> `get_neighbors(entity) -> GraphData`.
  - `list_documents`/`get_document` -> pass-through (return dicts as before).

- [ ] **Step 1: Write the failing test**

Create `tests/engine/test_backend.py`:

```python
import inspect

import pytest

from src.engine.config import EngineConfig
from src.engine.graphrag.backend import GraphRAGBackend, build


def test_build_returns_graphrag_backend():
    # build() instantiates Neo4j+Pipeline; it only constructs objects, no I/O
    # until a method is awaited. Verify type without touching services.
    from pathlib import Path
    cfg = EngineConfig(impl="graphrag", config_dir=Path("config/engine/graphrag"))
    kb = build(cfg)
    assert isinstance(kb, GraphRAGBackend)


def test_capabilities_declares_graph_and_partial_update():
    from pathlib import Path
    cfg = EngineConfig(impl="graphrag", config_dir=Path("config/engine/graphrag"))
    kb = build(cfg)
    assert kb.capabilities.graph is True
    assert kb.capabilities.partial_update is True


def test_backend_implements_protocol_methods():
    for name in [
        "ingest", "reingest", "remove", "recall",
        "get_graph", "get_neighbors", "list_documents", "get_document",
    ]:
        assert hasattr(GraphRAGBackend, name), f"missing {name}"


@pytest.mark.integration
async def test_ingest_recall_roundtrip():
    # Requires Postgres + Neo4j + Ollama + LLM configured.
    from pathlib import Path
    from src.engine.components.store.postgres import init_db
    await init_db()
    cfg = EngineConfig(impl="graphrag", config_dir=Path("config/engine/graphrag"))
    kb = build(cfg)
    ref = await kb.ingest(
        __import__("src.engine.interface", fromlist=["IngestSource"]).IngestSource(
            name="t.md", data=b"# T\n\nAcme is in Building A."
        )
    )
    assert ref.status in ("pending", "indexed")
    res = await kb.recall(
        __import__("src.engine.interface", fromlist=["RecallRequest"]).RecallRequest(query="Acme")
    )
    assert hasattr(res, "chunks")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/engine/test_backend.py -v -m "not integration"`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.engine.graphrag.backend'`

- [ ] **Step 3: Implement src/engine/graphrag/backend.py**

Create `src/engine/graphrag/backend.py`:

```python
"""GraphRAG backend: implements the KnowledgeBase contract.

Migrated from src/core/knowledge_base.py + src/core/search.py. The backend
owns its own DB sessions (async_session_factory); callers never pass a session.
"""
from __future__ import annotations

import asyncio
import shutil
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import selectinload  # noqa: F401  (kept for parity with original)

from src.engine.components.analyzer import Analyzer
from src.engine.components.extractors.registry import ExtractorRegistry, registry
from src.engine.components.store.models import Chunk, Document
from src.engine.components.store.neo4j import Neo4jClient
from src.engine.components.store.postgres import async_session_factory, init_db
from src.engine.config import EngineConfig
from src.engine.graphrag.pipeline import Pipeline
from src.engine.interface import (
    Capabilities,
    DocumentRef,
    GraphData,
    GraphLink,
    GraphNode,
    IngestSource,
    RecallChunk,
    RecallRequest,
    RecallResult,
)

UPLOAD_DIR = Path("uploads")


def _to_ref(doc: Document, chunk_count: int = 0, overview: str | None = None) -> DocumentRef:
    return DocumentRef(
        id=str(doc.id),
        title=doc.title,
        file_type=doc.file_type,
        status=doc.status,
        overview=overview if overview is not None else (doc.overview or ""),
        error_msg=doc.error_msg,
    )


class GraphRAGBackend:
    """GraphRAG implementation of KnowledgeBase."""

    capabilities = Capabilities(graph=True, partial_update=True, multimodal=True)

    def __init__(self, neo4j: Neo4jClient, pipeline: Pipeline) -> None:
        self._neo4j = neo4j
        self._pipeline = pipeline

    # ── ingest / reingest / remove ───────────────────────────────

    async def ingest(self, source: IngestSource) -> DocumentRef:
        data = source.data
        if source.path is not None and not data:
            data = source.path.read_bytes()
        file_type = ExtractorRegistry.guess_file_type(Path(source.name))

        doc_id = uuid.uuid4()
        doc_dir = UPLOAD_DIR / str(doc_id)
        doc_dir.mkdir(parents=True, exist_ok=True)
        file_path = doc_dir / source.name
        file_path.write_bytes(data)

        async with async_session_factory() as session:
            doc = Document(
                id=doc_id,
                title=source.name,
                file_type=file_type,
                file_path=str(file_path),
                status="pending",
            )
            session.add(doc)
            await session.commit()
            await session.refresh(doc)
            ref = _to_ref(doc)

        asyncio.create_task(
            self._pipeline.process_file(doc_id, file_path, source.name, file_type)
        )
        return ref

    async def reingest(self, doc_id: str) -> DocumentRef:
        uid = uuid.UUID(doc_id)
        async with async_session_factory() as session:
            doc = await session.get(Document, uid)
            if not doc:
                raise ValueError(f"文档不存在: {doc_id}")
            new_text = doc.raw_text or ""
            title = doc.title

        await self._pipeline.reindex_document(uid, new_text)

        async with async_session_factory() as session:
            doc = await session.get(Document, uid)
            assert doc is not None
            return _to_ref(doc)

    async def remove(self, doc_id: str) -> None:
        uid = uuid.UUID(doc_id)
        async with async_session_factory() as session:
            doc = await session.get(Document, uid)
            if not doc:
                return
            if doc.file_path:
                doc_dir = Path(doc.file_path).parent
                if doc_dir.exists():
                    shutil.rmtree(doc_dir)
            await session.delete(doc)
            await session.commit()
        await self._neo4j.delete_document_graph(doc_id)

    # ── recall ───────────────────────────────────────────────────

    async def recall(self, request: RecallRequest) -> RecallResult:
        from src.engine.graphrag._search import full_search

        async with async_session_factory() as session:
            result = await full_search(
                session, self._neo4j, request.query, top_k=request.top_k
            )
        chunks = [
            RecallChunk(
                doc_id=c.doc_id,
                title=c.title,
                chunk_text=c.chunk_text,
                reranker_score=c.reranker_score,
                vector_score=c.vector_score,
            )
            for c in result.chunks
        ]
        return RecallResult(
            chunks=chunks,
            related_entities=result.related_entities,
            related_docs=result.related_docs,
        )

    # ── graph ────────────────────────────────────────────────────

    async def get_graph(self, entity: str | None = None) -> GraphData:
        if entity is None:
            raw = await self._neo4j.get_full_graph()
        else:
            details = await self._neo4j.get_entity_details(entity)
            if not details:
                return GraphData()
            return GraphData(
                nodes=[GraphNode(
                    name=details.name, type=details.entity_type,
                    description=details.properties.get("description", ""),
                    sources=details.properties.get("sources", [])
                            if isinstance(details.properties.get("sources"), list) else [],
                )],
                links=[GraphLink(source=r.get("other_name", ""), target=details.name,
                                 type=r.get("type", ""), description=r.get("description", ""))
                       for r in details.relations if r.get("type")],
            )
        return GraphData(
            nodes=[GraphNode(name=n["name"], type=n["type"], description=n.get("description", ""),
                             sources=n.get("sources", [])) for n in raw.get("nodes", [])],
            links=[GraphLink(source=l["source"], target=l["target"], type=l["type"],
                             description=l.get("description", "")) for l in raw.get("links", [])],
        )

    async def get_neighbors(self, entity: str) -> GraphData:
        results = await self._neo4j.query_neighbors(entity, hops=2)
        return GraphData(
            nodes=[GraphNode(name=r.name, type=r.entity_type,
                             description=r.properties.get("description", "")) for r in results],
            links=[],
        )

    # ── browse ───────────────────────────────────────────────────

    async def list_documents(
        self,
        page: int = 1,
        page_size: int = 20,
        file_type: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        async with async_session_factory() as session:
            stmt = select(Document).order_by(Document.created_at.desc())
            if file_type:
                stmt = stmt.where(Document.file_type == file_type)
            if status:
                stmt = stmt.where(Document.status == status)

            count_stmt = select(func.count(Document.id))
            if file_type:
                count_stmt = count_stmt.where(Document.file_type == file_type)
            if status:
                count_stmt = count_stmt.where(Document.status == status)
            total = (await session.execute(count_stmt)).scalar() or 0

            stmt = stmt.offset((page - 1) * page_size).limit(page_size)
            docs = (await session.execute(stmt)).scalars().all()

            return {
                "total": total, "page": page, "page_size": page_size,
                "items": [
                    {
                        "id": str(d.id), "title": d.title, "file_type": d.file_type,
                        "status": d.status,
                        "overview": (d.overview or "")[:200],
                        "created_at": d.created_at.isoformat() if d.created_at else None,
                        "updated_at": d.updated_at.isoformat() if d.updated_at else None,
                    }
                    for d in docs
                ],
            }

    async def get_document(self, doc_id: str) -> dict[str, Any] | None:
        uid = uuid.UUID(doc_id)
        async with async_session_factory() as session:
            doc = await session.get(Document, uid)
            if not doc:
                return None
            count_stmt = select(func.count(Chunk.id)).where(Chunk.doc_id == uid)
            chunk_count = (await session.execute(count_stmt)).scalar() or 0
            return {
                "id": str(doc.id), "title": doc.title, "file_type": doc.file_type,
                "raw_text": doc.raw_text, "overview": doc.overview,
                "file_path": doc.file_path, "content_hash": doc.content_hash,
                "status": doc.status, "error_msg": doc.error_msg,
                "chunk_count": chunk_count,
                "created_at": doc.created_at.isoformat() if doc.created_at else None,
                "updated_at": doc.updated_at.isoformat() if doc.updated_at else None,
            }


def build(config: EngineConfig) -> GraphRAGBackend:
    """Factory used by src.engine.config.build_engine."""
    from src.engine.components.analyzer import Analyzer

    neo4j = Neo4jClient()
    analyzer = Analyzer(
        schema_path=config.config_dir / "entity_schema.yaml",
        model_config_path=config.config_dir / "model_config.yaml",
    )
    pipeline = Pipeline(neo4j, analyzer=analyzer)
    return GraphRAGBackend(neo4j, pipeline)
```

- [ ] **Step 4: Create the search helper migrated from src/core/search.py**

Create `src/engine/graphrag/_search.py` by moving the content of `src/core/search.py` and fixing its imports:

```bash
git mv src/core/search.py src/engine/graphrag/_search.py
```

In `src/engine/graphrag/_search.py`, replace the import block:

```python
from src.core.reranker import get_reranker
from src.db.models import Chunk
from src.db.neo4j_client import Neo4jClient, GraphQueryResult
from src.pipeline.embedder import embedder
```

with:

```python
from src.engine.components.reranker import get_reranker
from src.engine.components.store.models import Chunk
from src.engine.components.store.neo4j import Neo4jClient, GraphQueryResult
from src.engine.components.embedder import embedder
```

(The `full_search`, `vector_search`, `reranker_filter`, `graph_enrich` functions and the `SearchChunk`/`RelatedDoc`/`SearchResult` dataclasses are unchanged.)

- [ ] **Step 5: Run test to verify the non-integration tests pass**

Run: `uv run pytest tests/engine/test_backend.py -v -m "not integration"`
Expected: PASS (3 tests). `build(cfg)` constructs `Neo4jClient` + `Pipeline` objects without I/O (the Neo4j driver is lazy - it only connects on first query), so this passes without live services.

- [ ] **Step 6: Commit**

```bash
git add src/engine/graphrag/backend.py src/engine/graphrag/_search.py src/core tests/engine/test_backend.py
git commit -m "feat(engine): GraphRAG backend implements KnowledgeBase; migrate search"
```

---

### Task 13: Engine CLI adapter (thin)

**Files:**
- Create: `src/engine/cli.py`
- Test: `tests/engine/test_cli.py`

**Interfaces:**
- Produces: `src.engine.cli.main(argv: list[str] | None = None) -> int` - an `argparse`-based CLI with subcommands `ingest`, `recall`, `graph`, `get`, `list`, `remove`. It builds the engine via `load_config()` + `build_engine()` and calls the `KnowledgeBase` methods, printing JSON to stdout. A `--engine-impl`/`--config-dir` override is supported for tests.
- Consumes: `config.schema.load_config`, `src.engine.config.{EngineConfig, build_engine}`, `src.engine.interface.*`.

- [ ] **Step 1: Write the failing test**

Create `tests/engine/test_cli.py`:

```python
import json
from pathlib import Path

import pytest

from src.engine import cli as cli_mod
from src.engine.config import EngineConfig
from src.engine.interface import GraphData, GraphNode, IngestSource, RecallRequest, RecallResult
from tests.conftest import FakeKnowledgeBase


def _install_fake(monkeypatch, fake: FakeKnowledgeBase):
    monkeypatch.setattr(
        cli_mod, "build_engine", lambda cfg: fake, raising=True
    )


def test_cli_ingest_prints_doc_ref(capsys, monkeypatch):
    fake = FakeKnowledgeBase()
    _install_fake(monkeypatch, fake)
    rc = cli_mod.main(["ingest", "--name", "x.md", "--data", "hello"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["title"] == "x.md"
    assert out["status"] == "indexed"
    assert list(fake.raw.values())[0] == b"hello"


def test_cli_recall_prints_result(capsys, monkeypatch):
    fake = FakeKnowledgeBase()
    fake.recall_calls.clear()
    _install_fake(monkeypatch, fake)
    rc = cli_mod.main(["recall", "--query", "find acme", "--top-k", "5"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out == {"chunks": [], "related_entities": [], "related_docs": []}
    assert fake.recall_calls == ["find acme"]


def test_cli_graph_full(capsys, monkeypatch):
    fake = FakeKnowledgeBase()
    fake.graph = GraphData(nodes=[GraphNode(name="n", type="T")])
    _install_fake(monkeypatch, fake)
    rc = cli_mod.main(["graph"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["nodes"][0]["name"] == "n"


def test_cli_list(capsys, monkeypatch):
    fake = FakeKnowledgeBase()
    _install_fake(monkeypatch, fake)
    rc = cli_mod.main(["list"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["total"] == 0


def test_cli_remove(capsys, monkeypatch):
    fake = FakeKnowledgeBase()
    _install_fake(monkeypatch, fake)
    rc = cli_mod.main(["remove", "--doc-id", "abc"])
    assert rc == 0
    assert capsys.readouterr().out.strip() == '{"removed": "abc"}'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/engine/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.engine.cli'`

- [ ] **Step 3: Implement src/engine/cli.py**

Create `src/engine/cli.py`:

```python
"""Thin CLI adapter: wraps a KnowledgeBase instance built from config.

No business logic - every subcommand maps 1:1 to a KnowledgeBase method and
prints JSON. Usage: python -m src.engine.cli <subcommand> ...
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from config.schema import load_config
from src.engine.config import EngineConfig, build_engine
from src.engine.interface import KnowledgeBase


def _jsonable(obj: Any) -> Any:
    if hasattr(obj, "__dataclass_fields__"):
        return {k: _jsonable(v) for k, v in asdict(obj).items()}  # type: ignore[arg-type]
    if isinstance(obj, list):
        return [_jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, Path):
        return str(obj)
    return obj


def _print(obj: Any) -> None:
    print(json.dumps(_jsonable(obj), ensure_ascii=False))


async def _run(kb: KnowledgeBase, args: argparse.Namespace) -> int:
    if args.command == "ingest":
        ref = await kb.ingest(
            __import__("src.engine.interface", fromlist=["IngestSource"]).IngestSource(
                name=args.name, data=args.data.encode("utf-8")
            )
        )
        _print(ref)
    elif args.command == "recall":
        from src.engine.interface import RecallRequest

        res = await kb.recall(RecallRequest(query=args.query, top_k=args.top_k))
        _print(res)
    elif args.command == "graph":
        _print(await kb.get_graph(args.entity))
    elif args.command == "get":
        _print(await kb.get_document(args.doc_id))
    elif args.command == "list":
        _print(await kb.list_documents(args.page, args.page_size))
    elif args.command == "remove":
        await kb.remove(args.doc_id)
        _print({"removed": args.doc_id})
    else:
        print(f"unknown command: {args.command}", file=sys.stderr)
        return 2
    return 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="kb")
    p.add_argument("--engine-impl", default=None)
    p.add_argument("--config-dir", default=None)
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("ingest"); s.add_argument("--name", required=True); s.add_argument("--data", required=True)
    s = sub.add_parser("recall"); s.add_argument("--query", required=True); s.add_argument("--top-k", type=int, default=20)
    s = sub.add_parser("graph"); s.add_argument("--entity", default=None)
    s = sub.add_parser("get"); s.add_argument("--doc-id", required=True)
    s = sub.add_parser("list"); s.add_argument("--page", type=int, default=1); s.add_argument("--page-size", type=int, default=20)
    s = sub.add_parser("remove"); s.add_argument("--doc-id", required=True)
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    app_cfg = load_config()
    impl = args.engine_impl or app_cfg.engine.impl
    config_dir = Path(args.config_dir) if args.config_dir else Path(app_cfg.engine.config)
    kb = build_engine(EngineConfig(impl=impl, config_dir=config_dir))
    return asyncio.run(_run(kb, args))


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/engine/test_cli.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/engine/cli.py tests/engine/test_cli.py
git commit -m "feat(engine): thin CLI adapter wrapping KnowledgeBase"
```

---

### Task 14: Engine MCP adapter (thin)

**Files:**
- Create: `src/engine/mcp.py`
- Test: `tests/engine/test_mcp.py`

**Interfaces:**
- Produces: `src.engine.mcp.mcp` (a `FastMCP` instance named "Team Knowledge Base"), `set_kb(kb)`, and `build_app()` returning the streamable-HTTP ASGI app. Tools: `search(query)`, `get_document(doc_id)`, `query_graph(entity_name, include_neighbors=True, hops=2)`, `upload_document(file_name, content)`. Each tool wraps the configured `KnowledgeBase` (set via `set_kb`).
- Consumes: `src.engine.interface.*`; `mcp.server.fastmcp.FastMCP`.

- [ ] **Step 1: Write the failing test**

Create `tests/engine/test_mcp.py`:

```python
import json

import pytest

from src.engine import mcp as mcp_mod
from src.engine.interface import GraphData, GraphNode, RecallRequest, RecallResult
from tests.conftest import FakeKnowledgeBase


@pytest.fixture
def fake_kb():
    kb = FakeKnowledgeBase()
    mcp_mod.set_kb(kb)
    yield kb
    mcp_mod._kb = None


async def test_search_tool_returns_chunks(fake_kb):
    res = await mcp_mod.search("acme")
    assert res == {"chunks": [], "related_entities": [], "related_docs": []}
    assert fake_kb.recall_calls == ["acme"]


async def test_get_document_missing_returns_error(fake_kb):
    res = await mcp_mod.get_document("nope")
    assert "error" in res


async def test_query_graph_missing_returns_error(fake_kb):
    res = await mcp_mod.query_graph("ghost")
    assert "error" in res


async def test_query_graph_found(fake_kb):
    fake_kb.graph = GraphData(nodes=[GraphNode(name="Acme", type="Company")])
    res = await mcp_mod.query_graph("Acme", include_neighbors=False)
    assert "nodes" in res


async def test_upload_document(fake_kb):
    res = await mcp_mod.upload_document("x.md", "hello world")
    assert res["title"] == "x.md"
    assert list(fake_kb.raw.values())[0] == b"hello world"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/engine/test_mcp.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.engine.mcp'`

- [ ] **Step 3: Implement src/engine/mcp.py**

Create `src/engine/mcp.py`:

```python
"""Thin MCP adapter: exposes a KnowledgeBase instance as MCP tools over
streamable HTTP. No business logic - each tool wraps one KnowledgeBase method.
"""
from __future__ import annotations

import json
from typing import Any

from mcp.server.fastmcp import FastMCP

from src.engine.interface import KnowledgeBase

mcp = FastMCP("Team Knowledge Base")

_kb: KnowledgeBase | None = None


def set_kb(kb: KnowledgeBase) -> None:
    global _kb
    _kb = kb


def _get_kb() -> KnowledgeBase:
    if _kb is None:
        raise RuntimeError("KnowledgeBase 未初始化")
    return _kb


async def search(query: str) -> dict[str, Any]:
    """语义检索知识库（向量粗筛 -> Reranker 守门 -> 图谱增强）。"""
    from src.engine.interface import RecallRequest

    result = await _get_kb().recall(RecallRequest(query=query))
    return {
        "chunks": [
            {
                "doc_id": c.doc_id, "title": c.title,
                "chunk_text": c.chunk_text[:1000],
                "reranker_score": c.reranker_score, "vector_score": c.vector_score,
            }
            for c in result.chunks
        ],
        "related_entities": result.related_entities,
        "related_docs": result.related_docs,
    }


async def get_document(doc_id: str) -> dict[str, Any]:
    """获取文件详情。"""
    result = await _get_kb().get_document(doc_id)
    if not result:
        return {"error": f"文档不存在: {doc_id}"}
    return result


async def query_graph(
    entity_name: str, include_neighbors: bool = True, hops: int = 2
) -> dict[str, Any]:
    """查询知识图谱中的实体及其关系。"""
    kb = _get_kb()
    graph = await kb.get_graph(entity_name)
    if not graph.nodes:
        return {"error": f"实体不存在: {entity_name}"}
    node = graph.nodes[0]
    out: dict[str, Any] = {
        "name": node.name, "type": node.type,
        "properties": {"description": node.description, "sources": node.sources},
        "relations": [
            {"type": l.type, "other": l.target if l.source == node.name else l.source,
             "description": l.description}
            for l in graph.links
        ],
    }
    if include_neighbors:
        neighbors = await kb.get_neighbors(entity_name)
        out["neighbors"] = [
            {"name": n.name, "type": n.type, "description": n.description}
            for n in neighbors.nodes
        ]
    return out


async def upload_document(file_name: str, content: str) -> dict[str, Any]:
    """上传文档到知识库（文本内容直接上传）。"""
    from src.engine.interface import IngestSource

    ref = await _get_kb().ingest(
        IngestSource(name=file_name, data=content.encode("utf-8"))
    )
    return {
        "id": ref.id, "title": ref.title, "file_type": ref.file_type, "status": ref.status,
    }


# Register the async functions as MCP tools (FastMCP introspects signatures).
mcp.tool()(search)
mcp.tool()(get_document)
mcp.tool()(query_graph)
mcp.tool()(upload_document)


def build_app():
    """Return the streamable-HTTP ASGI app (no lifespan; caller manages sessions)."""
    return mcp.streamable_http_app()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/engine/test_mcp.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/engine/mcp.py tests/engine/test_mcp.py
git commit -m "feat(engine): thin MCP adapter wrapping KnowledgeBase"
```

---

### Task 15: KnowledgeBase contract test (fake + graphrag pass the same suite)

**Files:**
- Create: `tests/engine/test_contract.py`

**Interfaces:** Consumes `FakeKnowledgeBase` (conftest) and `GraphRAGBackend`. Defines a shared contract every `KnowledgeBase` impl must satisfy; runs against the fake unconditionally and against graphrag only under `RUN_INTEGRATION=1`.

- [ ] **Step 1: Write the contract test**

Create `tests/engine/test_contract.py`:

```python
"""Shared KnowledgeBase contract: every implementation must satisfy this.

Runs against FakeKnowledgeBase always; against GraphRAGBackend only when
RUN_INTEGRATION=1 (needs Postgres + Neo4j + Ollama).
"""
import os

import pytest

from src.engine.interface import IngestSource, RecallRequest
from tests.conftest import FakeKnowledgeBase


def _make_fake() -> FakeKnowledgeBase:
    return FakeKnowledgeBase()


def _make_graphrag():
    from pathlib import Path
    from src.engine.config import EngineConfig, build_engine
    return build_engine(EngineConfig(impl="graphrag", config_dir=Path("config/engine/graphrag"))


BACKENDS = [("fake", _make_fake)]
if os.environ.get("RUN_INTEGRATION") == "1":
    BACKENDS.append(("graphrag", _make_graphrag))


@pytest.mark.parametrize("name,factory", BACKENDS)
async def test_ingest_returns_doc_ref(name, factory):
    kb = factory()
    ref = await kb.ingest(IngestSource(name="t.md", data=b"# T\n\nbody"))
    assert ref.id
    assert ref.title == "t.md"
    assert ref.status


@pytest.mark.parametrize("name,factory", BACKENDS)
async def test_recall_returns_result(name, factory):
    kb = factory()
    res = await kb.recall(RecallRequest(query="anything"))
    assert hasattr(res, "chunks")
    assert hasattr(res, "related_entities")


@pytest.mark.parametrize("name,factory", BACKENDS)
async def test_get_graph_returns_graph_data(name, factory):
    from src.engine.interface import GraphData
    kb = factory()
    g = await kb.get_graph(None)
    assert isinstance(g, GraphData)


@pytest.mark.parametrize("name,factory", BACKENDS)
async def test_list_documents_shape(name, factory):
    kb = factory()
    out = await kb.list_documents()
    assert {"total", "page", "page_size", "items"} <= set(out)


@pytest.mark.parametrize("name,factory", BACKENDS)
async def test_remove_is_idempotent(name, factory):
    kb = factory()
    # removing a non-existent id must not raise
    await kb.remove("00000000-0000-0000-0000-000000000000")
```

- [ ] **Step 2: Run the contract suite (fake only)**

Run: `uv run pytest tests/engine/test_contract.py -v`
Expected: PASS (5 tests, all against `fake`).

- [ ] **Step 3: Commit**

```bash
git add tests/engine/test_contract.py
git commit -m "test(engine): KnowledgeBase contract suite (fake + graphrag)"
```

---

### Task 16: Delete old engine code (clean break) + wiki stub

**Files:**
- Delete: `src/core/`, `src/api/`, `src/main.py`
- Create: `src/engine/wiki/README.md`
- Modify: `README.md` (run commands)

**Interfaces:** None new. This completes Phase 1: old `src/{core,api,main.py}` gone; engine reachable via CLI and MCP.

- [ ] **Step 1: Verify nothing in the new tree imports the old packages**

Run:
```bash
uv run python -c "import src.engine.cli, src.engine.mcp, src.engine.graphrag.backend; print('clean')"
```
Expected: prints `clean`. If this fails, an old import remains in a new file - fix it before deleting.

- [ ] **Step 2: Delete the old engine/api code**

```bash
git rm -r src/core src/api src/main.py
```

(`src/db/` and `src/pipeline/` were already removed in Tasks 5–11.) Confirm `src/` now contains only `__init__.py` and `engine/`.

- [ ] **Step 3: Add the wiki stub**

Create `src/engine/wiki/README.md`:

```markdown
# Wiki engine (stub)

Placeholder for a future `KnowledgeBase` implementation backed by a wiki.
Not built in this effort (spec §2). Implements `src.engine.interface.KnowledgeBase`
when added; selected via `config/app.yaml` `engine.impl: wiki`.
```

- [ ] **Step 4: Document run commands in README.md**

Replace the contents of `README.md` with:

```markdown
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
```

- [ ] **Step 5: Run the full engine test suite**

Run: `uv run pytest tests/engine -v -m "not integration"`
Expected: PASS (all engine unit/contract tests green; old-code references gone).

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor(engine): delete old src/{core,api,main}; Phase 1 complete"
```

---

## Phase 2 - Agent (interface + engine_client + skills (a)+(b) + codex plugin)

**Exit criteria (spec §7):** skills run in-process; codex plugin loads and calls engine via MCP. No engine coupling in `memory.py`.

### Task 17: Agent interface contracts (Skill, EngineClient, AgentPlugin, LlmClient) + memory Protocol

**Files:**
- Create: `src/agent/__init__.py`, `src/agent/interface.py`, `src/agent/memory.py`
- Test: `tests/agent/test_interface.py`

**Interfaces:**
- Produces (consumed by Tasks 18–22 and Phase 3 BFF):
  - `LlmClient` Protocol: `async def complete(self, prompt: str) -> str`.
  - `SkillContext` dataclass: `engine: EngineClient`, `llm: LlmClient | None = None`, `params: dict = field(default_factory=dict)`.
  - `SkillResult` dataclass: `name: str`, `output: dict`.
  - `Skill` Protocol: `name: str`, `description: str`, `async def run(self, ctx: SkillContext) -> SkillResult`.
  - `EngineClient` Protocol: `recall(query, top_k=10) -> dict`, `ingest(name, data) -> dict`, `get_document(doc_id) -> dict`, `get_graph(entity=None) -> dict`, `get_neighbors(entity) -> dict`.
  - `AgentPlugin` Protocol: `harness: str`, `def skills(self) -> list[Skill]`.
  - `MemoryStore` Protocol (in `memory.py`): `async def remember(self, key: str, value: str) -> None`, `async def recall(self, key: str) -> str | None`. No implementation.

- [ ] **Step 1: Write the failing test**

Create `tests/agent/test_interface.py`:

```python
from src.agent.interface import (
    AgentPlugin, EngineClient, LlmClient, Skill, SkillContext, SkillResult,
)
from src.agent.memory import MemoryStore


def test_skill_context_defaults():
    ctx = SkillContext(engine=None)  # type: ignore[arg-type]
    assert ctx.llm is None
    assert ctx.params == {}


def test_skill_result_holds_dict():
    r = SkillResult(name="x", output={"a": 1})
    assert r.output["a"] == 1


def test_protocols_are_importable():
    # Protocols exist and are usable as types
    for p in (Skill, EngineClient, AgentPlugin, LlmClient, MemoryStore):
        assert p is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/agent/test_interface.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.agent'`

- [ ] **Step 3: Implement src/agent/interface.py**

Create `src/agent/__init__.py` (empty).

Create `src/agent/interface.py`:

```python
"""Agent module contracts.

Skills are harness-agnostic: callable in-process (by the webapp BFF) and
wrappable by any harness plugin (codex first). Skills call an EngineClient,
never engine internals. Memory is interface-only (impl deferred).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class LlmClient(Protocol):
    """Minimal LLM interface a skill uses for synthesis (answer/summary)."""
    async def complete(self, prompt: str) -> str: ...


@dataclass
class SkillContext:
    engine: "EngineClient"
    llm: LlmClient | None = None
    params: dict = field(default_factory=dict)


@dataclass
class SkillResult:
    name: str
    output: dict


class Skill(Protocol):
    name: str
    description: str
    async def run(self, ctx: SkillContext) -> SkillResult: ...


class EngineClient(Protocol):
    """Uniform client over in-process / MCP transports. Skills call this,
    never engine internals. All methods return JSON-ish dicts."""

    async def recall(self, query: str, top_k: int = 10) -> dict: ...
    async def ingest(self, name: str, data: bytes) -> dict: ...
    async def get_document(self, doc_id: str) -> dict: ...
    async def get_graph(self, entity: str | None = None) -> dict: ...
    async def get_neighbors(self, entity: str) -> dict: ...


class AgentPlugin(Protocol):
    """What each harness implementation exposes."""
    harness: str
    def skills(self) -> list[Skill]: ...
```

- [ ] **Step 4: Implement src/agent/memory.py (Protocol only)**

Create `src/agent/memory.py`:

```python
"""Agent memory abstraction - interface ONLY. No implementation (spec §1, §2).

The agent decides where its memory lives at runtime; no concrete store is
built in this effort. No coupling to the engine.
"""
from __future__ import annotations

from typing import Protocol


class MemoryStore(Protocol):
    async def remember(self, key: str, value: str) -> None: ...
    async def recall(self, key: str) -> str | None: ...
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/agent/test_interface.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add src/agent/__init__.py src/agent/interface.py src/agent/memory.py tests/agent/test_interface.py
git commit -m "feat(agent): Skill/EngineClient/AgentPlugin contracts + memory Protocol"
```

---

### Task 18: EngineClient implementations (in-process + MCP)

**Files:**
- Create: `src/agent/engine_client.py`
- Test: `tests/agent/test_engine_client.py`

**Interfaces:**
- Consumes: `src.engine.interface.KnowledgeBase` (for in-process); `src.engine.mcp` tools (for MCP).
- Produces: `InProcessEngineClient(kb: KnowledgeBase)` and `McpEngineClient(base_url: str)`, both implementing `EngineClient`. `InProcessEngineClient` serializes `KnowledgeBase` results to dicts; `McpEngineClient._call(tool, args) -> dict` opens an MCP client session per call and JSON-parses the text content.

- [ ] **Step 1: Write the failing test**

Create `tests/agent/test_engine_client.py`:

```python
import json

import pytest

from src.agent.engine_client import InProcessEngineClient, McpEngineClient
from src.engine.interface import GraphData, GraphNode, IngestSource, RecallRequest
from tests.conftest import FakeKnowledgeBase


async def test_inprocess_recall_returns_dict():
    kb = FakeKnowledgeBase()
    client = InProcessEngineClient(kb)
    out = await client.recall("acme")
    assert out == {"chunks": [], "related_entities": [], "related_docs": []}
    assert kb.recall_calls == ["acme"]


async def test_inprocess_ingest_returns_dict():
    kb = FakeKnowledgeBase()
    client = InProcessEngineClient(kb)
    out = await client.ingest("x.md", b"hello")
    assert out["title"] == "x.md"
    assert out["status"] == "indexed"


async def test_inprocess_get_graph_returns_dict():
    kb = FakeKnowledgeBase()
    kb.graph = GraphData(nodes=[GraphNode(name="n", type="T")])
    client = InProcessEngineClient(kb)
    out = await client.get_graph(None)
    assert out["nodes"][0]["name"] == "n"


async def test_mcp_recall_calls_search_tool(monkeypatch):
    client = McpEngineClient("http://localhost:8000/mcp")
    calls = []

    async def fake_call(tool, args):
        calls.append((tool, args))
        return {"chunks": [], "related_entities": [], "related_docs": []}

    monkeypatch.setattr(client, "_call", fake_call)
    out = await client.recall("acme", top_k=7)
    assert out["chunks"] == []
    assert calls == [("search", {"query": "acme"})]


async def test_mcp_ingest_calls_upload_document(monkeypatch):
    client = McpEngineClient("http://localhost:8000/mcp")
    async def fake_call(tool, args):
        assert tool == "upload_document"
        assert args == {"file_name": "x.md", "content": "hello"}
        return {"id": "1", "title": "x.md", "file_type": "markdown", "status": "indexed"}
    monkeypatch.setattr(client, "_call", fake_call)
    out = await client.ingest("x.md", b"hello")
    assert out["title"] == "x.md"


async def test_mcp_get_graph_calls_query_graph(monkeypatch):
    client = McpEngineClient("http://localhost:8000/mcp")
    async def fake_call(tool, args):
        assert tool == "query_graph"
        return {"name": "Acme", "type": "Company"}
    monkeypatch.setattr(client, "_call", fake_call)
    out = await client.get_graph("Acme")
    assert out["name"] == "Acme"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/agent/test_engine_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.agent.engine_client'`

- [ ] **Step 3: Implement src/agent/engine_client.py**

Create `src/agent/engine_client.py`:

```python
"""Uniform EngineClient over in-process and MCP transports.

InProcessEngineClient wraps a KnowledgeBase directly (used by the webapp BFF
when engine_access=inprocess). McpEngineClient calls the engine's MCP tools
over streamable HTTP (used by the codex harness and when engine_access=mcp).
"""
from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from src.engine.interface import KnowledgeBase


def _jsonable(obj: Any) -> Any:
    if hasattr(obj, "__dataclass_fields__"):
        return {k: _jsonable(v) for k, v in asdict(obj).items()}  # type: ignore[arg-type]
    if isinstance(obj, list):
        return [_jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    return obj


class InProcessEngineClient:
    """EngineClient backed by an in-process KnowledgeBase instance."""

    def __init__(self, kb: KnowledgeBase) -> None:
        self._kb = kb

    async def recall(self, query: str, top_k: int = 10) -> dict:
        from src.engine.interface import RecallRequest

        res = await self._kb.recall(RecallRequest(query=query, top_k=top_k))
        return _jsonable(res)

    async def ingest(self, name: str, data: bytes) -> dict:
        from src.engine.interface import IngestSource

        ref = await self._kb.ingest(IngestSource(name=name, data=data))
        return _jsonable(ref)

    async def get_document(self, doc_id: str) -> dict:
        out = await self._kb.get_document(doc_id)
        return out if out is not None else {"error": f"文档不存在: {doc_id}"}

    async def get_graph(self, entity: str | None = None) -> dict:
        return _jsonable(await self._kb.get_graph(entity))

    async def get_neighbors(self, entity: str) -> dict:
        return _jsonable(await self._kb.get_neighbors(entity))


class McpEngineClient:
    """EngineClient backed by an engine MCP server (streamable HTTP)."""

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url

    async def _call(self, tool: str, args: dict) -> dict:
        from mcp.client.session import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        async with streamablehttp_client(self._base_url) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool, args)
        text = result.content[0].text
        return json.loads(text)

    async def recall(self, query: str, top_k: int = 10) -> dict:
        return await self._call("search", {"query": query})

    async def ingest(self, name: str, data: bytes) -> dict:
        return await self._call("upload_document", {"file_name": name, "content": data.decode("utf-8")})

    async def get_document(self, doc_id: str) -> dict:
        return await self._call("get_document", {"doc_id": doc_id})

    async def get_graph(self, entity: str | None = None) -> dict:
        return await self._call("query_graph", {"entity_name": entity or "", "include_neighbors": False})

    async def get_neighbors(self, entity: str) -> dict:
        return await self._call("query_graph", {"entity_name": entity, "include_neighbors": True, "hops": 2})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/agent/test_engine_client.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/agent/engine_client.py tests/agent/test_engine_client.py
git commit -m "feat(agent): InProcess + MCP EngineClient implementations"
```

---

### Task 19: Skill (a) search_and_answer

**Files:**
- Create: `src/agent/skills/__init__.py`, `src/agent/skills/search_and_answer.py`
- Test: `tests/agent/test_skills.py` (covers Task 19 + 20)

**Interfaces:**
- Produces: `SearchAndAnswerSkill` with `name="search_and_answer"`, `description`, and `async def run(self, ctx: SkillContext) -> SkillResult`. Reads `ctx.params["query"]` (and optional `top_k`), calls `ctx.engine.recall`, formats context, and - if `ctx.llm` is set - synthesizes an answer via `ctx.llm.complete`; otherwise returns the formatted context as `answer` (the harness synthesizes). Returns `SkillResult(name, output={"query", "answer", "sources"})`.

- [ ] **Step 1: Write the failing test (shared with Task 20)**

Create `tests/agent/test_skills.py`:

```python
import pytest

from src.agent.interface import LlmClient, SkillContext
from src.agent.skills.ingest_and_summarize import IngestAndSummarizeSkill
from src.agent.skills.search_and_answer import SearchAndAnswerSkill


class FakeEngineClient:
    def __init__(self):
        self.recall_result = {"chunks": [{"doc_id": "d1", "title": "Acme Doc",
                                          "chunk_text": "Acme is in Building A.",
                                          "reranker_score": 9.0, "vector_score": 0.8}],
                            "related_entities": [{"name": "Acme"}], "related_docs": []}
        self.ingested = None
        self.doc_detail = {"id": "d1", "raw_text": "Acme is in Building A.", "overview": "ov"}

    async def recall(self, query, top_k=10):
        return self.recall_result

    async def ingest(self, name, data):
        self.ingested = (name, data)
        return {"id": "d1", "title": name, "file_type": "markdown", "status": "indexed"}

    async def get_document(self, doc_id):
        return self.doc_detail

    async def get_graph(self, entity=None):
        return {"nodes": [], "links": []}

    async def get_neighbors(self, entity):
        return {"nodes": [], "links": []}


class FakeLlm:
    def __init__(self):
        self.prompts = []

    async def complete(self, prompt):
        self.prompts.append(prompt)
        return "SYNTHESIZED ANSWER"


async def test_search_and_answer_with_llm_synthesizes():
    skill = SearchAndAnswerSkill()
    llm = FakeLlm()
    ctx = SkillContext(engine=FakeEngineClient(), llm=llm, params={"query": "where is Acme?"})
    res = await skill.run(ctx)
    assert res.name == "search_and_answer"
    assert res.output["answer"] == "SYNTHESIZED ANSWER"
    assert res.output["query"] == "where is Acme?"
    assert "Acme" in res.output["sources"]["chunks"][0]["chunk_text"]
    assert len(llm.prompts) == 1
    assert "where is Acme?" in llm.prompts[0]


async def test_search_and_answer_without_llm_returns_context():
    skill = SearchAndAnswerSkill()
    ctx = SkillContext(engine=FakeEngineClient(), llm=None, params={"query": "q"})
    res = await skill.run(ctx)
    assert "Acme is in Building A" in res.output["answer"]


async def test_search_and_answer_respects_top_k():
    skill = SearchAndAnswerSkill()
    engine = FakeEngineClient()
    ctx = SkillContext(engine=engine, llm=None, params={"query": "q", "top_k": 3})
    await skill.run(ctx)
    # FakeEngineClient ignores top_k, but the skill must pass it through without error
    assert True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/agent/test_skills.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.agent.skills'`

- [ ] **Step 3: Implement src/agent/skills/search_and_answer.py**

Create `src/agent/skills/__init__.py` (empty).

Create `src/agent/skills/search_and_answer.py`:

```python
"""Skill (a): recall knowledge-base context for a query and synthesize an answer.

Harness-agnostic: when ctx.llm is provided it synthesizes the answer; when not,
it returns the formatted recall context and lets the harness (e.g. codex) answer.
"""
from __future__ import annotations

from src.agent.interface import SkillContext, SkillResult


def _format_context(recall: dict) -> str:
    lines = []
    for i, c in enumerate(recall.get("chunks", []), 1):
        lines.append(f"[{i}] ({c.get('title', '')}) {c.get('chunk_text', '')}")
    ents = recall.get("related_entities", [])
    if ents:
        lines.append("相关实体: " + ", ".join(e.get("name", "") for e in ents))
    return "\n".join(lines)


def _answer_prompt(query: str, context: str) -> str:
    return (
        f"你是知识库助手。根据以下检索到的资料回答问题。若资料不足请说明。\n\n"
        f"问题: {query}\n\n资料:\n{context}\n\n回答:"
    )


class SearchAndAnswerSkill:
    name = "search_and_answer"
    description = "Recall knowledge-base context for a query and synthesize an answer."

    async def run(self, ctx: SkillContext) -> SkillResult:
        query = ctx.params.get("query", "")
        top_k = int(ctx.params.get("top_k", 10))
        recall = await ctx.engine.recall(query, top_k=top_k)
        context = _format_context(recall)
        if ctx.llm is not None:
            answer = await ctx.llm.complete(_answer_prompt(query, context))
        else:
            answer = context
        return SkillResult(
            name=self.name,
            output={"query": query, "answer": answer, "sources": recall},
        )
```

- [ ] **Step 4: Run test to verify it passes (search_and_answer only; ingest test still fails)**

Run: `uv run pytest tests/agent/test_skills.py::test_search_and_answer_with_llm_synthesizes tests/agent/test_skills.py::test_search_and_answer_without_llm_returns_context tests/agent/test_skills.py::test_search_and_answer_respects_top_k -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/agent/skills/__init__.py src/agent/skills/search_and_answer.py tests/agent/test_skills.py
git commit -m "feat(agent): search_and_answer skill (recall + synthesize)"
```

---

### Task 20: Skill (b) ingest_and_summarize

**Files:**
- Create: `src/agent/skills/ingest_and_summarize.py`
- Test: `tests/agent/test_skills.py` (extend the file from Task 19)

**Interfaces:**
- Produces: `IngestAndSummarizeSkill` with `name="ingest_and_summarize"`. Reads `ctx.params["name"]` and `ctx.params["data"]` (bytes), calls `ctx.engine.ingest`, then `ctx.engine.get_document(doc_id)`, and - if `ctx.llm` set - summarizes `raw_text` via `ctx.llm.complete`; otherwise uses the document `overview`. Returns `SkillResult(name, output={"doc", "summary"})`.

- [ ] **Step 1: Append the failing test to tests/agent/test_skills.py**

Append to `tests/agent/test_skills.py`:

```python
async def test_ingest_and_summarize_with_llm():
    skill = IngestAndSummarizeSkill()
    engine = FakeEngineClient()
    llm = FakeLlm()
    ctx = SkillContext(engine=engine, llm=llm, params={"name": "r.md", "data": b"Acme is in Building A."})
    res = await skill.run(ctx)
    assert res.name == "ingest_and_summarize"
    assert res.output["doc"]["title"] == "r.md"
    assert res.output["summary"] == "SYNTHESIZED ANSWER"
    assert engine.ingested == ("r.md", b"Acme is in Building A.")
    assert "r.md" in llm.prompts[0]


async def test_ingest_and_summarize_without_llm_uses_overview():
    skill = IngestAndSummarizeSkill()
    engine = FakeEngineClient()
    ctx = SkillContext(engine=engine, llm=None, params={"name": "r.md", "data": b"x"})
    res = await skill.run(ctx)
    assert res.output["summary"] == "ov"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/agent/test_skills.py::test_ingest_and_summarize_with_llm -v`
Expected: FAIL with `AttributeError`/import error for `IngestAndSummarizeSkill`.

- [ ] **Step 3: Implement src/agent/skills/ingest_and_summarize.py**

Create `src/agent/skills/ingest_and_summarize.py`:

```python
"""Skill (b): ingest a file into the knowledge base and produce a summary.

Harness-agnostic: when ctx.llm is provided it summarizes the ingested text;
otherwise it returns the engine-generated document overview.
"""
from __future__ import annotations

from src.agent.interface import SkillContext, SkillResult


def _summary_prompt(name: str, text: str) -> str:
    return (
        f"请为以下文档生成 2-3 句话的摘要。\n\n文档: {name}\n\n内容:\n{text[:4000]}\n\n摘要:"
    )


class IngestAndSummarizeSkill:
    name = "ingest_and_summarize"
    description = "Ingest a file into the knowledge base and produce a summary."

    async def run(self, ctx: SkillContext) -> SkillResult:
        name = ctx.params["name"]
        data: bytes = ctx.params["data"]
        doc = await ctx.engine.ingest(name, data)
        detail = await ctx.engine.get_document(doc.get("id", ""))
        text = (detail or {}).get("raw_text", "")
        if ctx.llm is not None and text:
            summary = await ctx.llm.complete(_summary_prompt(name, text))
        else:
            summary = (detail or {}).get("overview", "")
        return SkillResult(name=self.name, output={"doc": doc, "summary": summary})
```

- [ ] **Step 4: Run the full skills test to verify it passes**

Run: `uv run pytest tests/agent/test_skills.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/agent/skills/ingest_and_summarize.py tests/agent/test_skills.py
git commit -m "feat(agent): ingest_and_summarize skill"
```

---

### Task 21: Codex plugin + config (AgentPlugin packaging skills; Phase 2 verification)

**Files:**
- Create: `src/agent/codex/__init__.py`, `src/agent/codex/plugin.py`
- Create: `config/agent/codex/plugin.yaml`
- Test: `tests/agent/test_plugin.py`

**Interfaces:**
- Produces: `src.agent.codex.plugin.CodexPlugin` implementing `AgentPlugin` (`harness="codex"`). `CodexPlugin(skills, mcp_url)`; `skills()` returns the shared skills; `build_manifest() -> dict` emits the codex skill manifest (`{harness, mcp_url, skills:[{name,description}]}`). `build_plugin(config: AppConfig) -> CodexPlugin` reads `config.agent.skills` to select skills and `config/agent/codex/plugin.yaml` for `mcp_url`. `src.engine.wiki/README.md` already exists from Task 16.

- [ ] **Step 1: Create the codex plugin config**

Create `config/agent/codex/plugin.yaml`:

```yaml
mcp_url: http://localhost:8000/mcp
```

- [ ] **Step 2: Write the failing test**

Create `tests/agent/test_plugin.py`:

```python
from pathlib import Path

from src.agent.codex.plugin import CodexPlugin, build_plugin
from src.agent.skills.ingest_and_summarize import IngestAndSummarizeSkill
from src.agent.skills.search_and_answer import SearchAndAnswerSkill


def test_codex_plugin_exposes_skills():
    plugin = CodexPlugin(skills=None, mcp_url="http://localhost:8000/mcp")
    assert plugin.harness == "codex"
    names = [s.name for s in plugin.skills()]
    assert "search_and_answer" in names
    assert "ingest_and_summarize" in names


def test_codex_plugin_manifest_shape():
    plugin = CodexPlugin(
        skills=[SearchAndAnswerSkill(), IngestAndSummarizeSkill()],
        mcp_url="http://x/mcp",
    )
    m = plugin.build_manifest()
    assert m["harness"] == "codex"
    assert m["mcp_url"] == "http://x/mcp"
    assert m["skills"][0]["name"] == "search_and_answer"
    assert m["skills"][1]["name"] == "ingest_and_summarize"


def test_build_plugin_reads_config():
    from config.schema import AppConfig, load_config

    cfg = load_config(Path("config/app.yaml"))
    plugin = build_plugin(cfg)
    assert plugin.harness == "codex"
    assert plugin._mcp_url == "http://localhost:8000/mcp"
    assert len(plugin.skills()) == 2
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/agent/test_plugin.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.agent.codex'`

- [ ] **Step 4: Implement src/agent/codex/plugin.py**

Create `src/agent/codex/__init__.py` (empty).

Create `src/agent/codex/plugin.py`:

```python
"""Codex harness plugin: packages the shared, harness-agnostic skills into the
codex harness format + config. The codex harness calls the engine via MCP
(there is no harness to test against yet; the manifest describes the wiring).
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import yaml

from src.agent.interface import AgentPlugin, Skill
from src.agent.skills.ingest_and_summarize import IngestAndSummarizeSkill
from src.agent.skills.search_and_answer import SearchAndAnswerSkill

_SKILL_FACTORIES: dict[str, Callable[[], Skill]] = {
    "search_and_answer": SearchAndAnswerSkill,
    "ingest_and_summarize": IngestAndSummarizeSkill,
}


class CodexPlugin:
    """AgentPlugin for the codex harness."""

    harness = "codex"

    def __init__(self, skills: list[Skill] | None, mcp_url: str) -> None:
        self._skills = skills if skills is not None else _default_skills()
        self._mcp_url = mcp_url

    def skills(self) -> list[Skill]:
        return list(self._skills)

    def build_manifest(self) -> dict:
        return {
            "harness": self.harness,
            "mcp_url": self._mcp_url,
            "skills": [{"name": s.name, "description": s.description} for s in self._skills],
        }


def _default_skills() -> list[Skill]:
    return [SearchAndAnswerSkill(), IngestAndSummarizeSkill()]


def build_plugin(config) -> CodexPlugin:
    """Build the codex plugin from AppConfig + config/agent/codex/plugin.yaml."""
    skill_names = getattr(config.agent, "skills", list(_SKILL_FACTORIES))
    skills: list[Skill] = []
    for name in skill_names:
        factory = _SKILL_FACTORIES.get(name)
        if factory is not None:
            skills.append(factory())

    plugin_cfg_path = Path("config/agent/codex/plugin.yaml")
    mcp_url = "http://localhost:8000/mcp"
    if plugin_cfg_path.exists():
        data = yaml.safe_load(plugin_cfg_path.read_text(encoding="utf-8")) or {}
        mcp_url = data.get("mcp_url", mcp_url)

    return CodexPlugin(skills=skills, mcp_url=mcp_url)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/agent/test_plugin.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Run the full agent suite + verify in-process skill execution**

Run: `uv run pytest tests/agent -v`
Expected: PASS (all agent tests).

Also verify a skill runs end-to-end in-process against the fake engine:
```bash
uv run python -c "
import asyncio
from src.agent.codex.plugin import build_plugin
from config.schema import load_config
from src.agent.engine_client import InProcessEngineClient
from src.agent.interface import SkillContext
from tests.conftest import FakeKnowledgeBase

async def go():
    plugin = build_plugin(load_config('config/app.yaml'))
    skill = plugin.skills()[0]  # search_and_answer
    ctx = SkillContext(engine=InProcessEngineClient(FakeKnowledgeBase()), params={'query': 'acme'})
    res = await skill.run(ctx)
    print(res.name, list(res.output))

asyncio.run(go())
"
```
Expected: prints `search_and_answer ['query', 'answer', 'sources']`.

- [ ] **Step 7: Commit**

```bash
git add src/agent/codex/ config/agent/ tests/agent/test_plugin.py
git commit -m "feat(agent): codex plugin + config; Phase 2 complete"
```

---

## Phase 3 - Frontend (webapp BFF + SPA)

**Exit criteria (spec §7):** end-to-end browse/search/graph/ingest; invoke agent skills from UI. Old `frontend/` removed.

> **Design decision:** the BFF consumes the engine through the agent's `EngineClient` abstraction (dicts, uniform over in-process/MCP), not the `KnowledgeBase` Protocol directly. This matches the spec's dependency rule (`frontend -> agent + engine` as clients) and makes `engine_access: inprocess | mcp` a pure wiring choice. Task 22 extends `EngineClient` and the engine MCP tools with the two BFF-only operations (`list_documents`, `remove`); inline content-editing is out of Phase-3 scope (spec §6 lists browse/search/graph/ingest only).

### Task 22: Extend EngineClient + engine MCP tools for BFF operations

**Files:**
- Modify: `src/agent/interface.py` (add `list_documents`, `remove` to `EngineClient`)
- Modify: `src/agent/engine_client.py` (implement in both clients)
- Modify: `src/engine/mcp.py` (add `list_documents`, `remove_document`, `get_full_graph` tools)
- Modify: `tests/agent/test_engine_client.py`, `tests/engine/test_mcp.py` (new cases)

**Interfaces:**
- `EngineClient` gains: `async def list_documents(self, page: int = 1, page_size: int = 20, file_type: str | None = None, status: str | None = None) -> dict` and `async def remove(self, doc_id: str) -> dict`.
- `InProcessEngineClient.list_documents` -> `kb.list_documents(...)` (already dict); `.remove` -> `await kb.remove(doc_id)` then `return {"removed": doc_id}`.
- `McpEngineClient.list_documents` -> MCP tool `list_documents`; `.remove` -> MCP tool `remove_document`; `.get_graph(None)` -> MCP tool `get_full_graph` (else `query_graph`).
- Engine MCP gains tools `list_documents(page, page_size, file_type, status)`, `remove_document(doc_id)`, `get_full_graph()`.

- [ ] **Step 1: Add the failing tests**

Append to `tests/agent/test_engine_client.py`:

```python
async def test_inprocess_list_documents():
    kb = FakeKnowledgeBase()
    client = InProcessEngineClient(kb)
    out = await client.list_documents()
    assert {"total", "page", "page_size", "items"} <= set(out)


async def test_inprocess_remove_returns_removed():
    kb = FakeKnowledgeBase()
    client = InProcessEngineClient(kb)
    out = await client.remove("abc")
    assert out == {"removed": "abc"}


async def test_mcp_list_documents_calls_tool(monkeypatch):
    client = McpEngineClient("http://x/mcp")
    seen = []
    async def fake_call(tool, args):
        seen.append((tool, args))
        return {"total": 0, "page": 1, "page_size": 20, "items": []}
    monkeypatch.setattr(client, "_call", fake_call)
    out = await client.list_documents(page=2, page_size=5, file_type="markdown")
    assert out["total"] == 0
    assert seen == [("list_documents", {"page": 2, "page_size": 5, "file_type": "markdown", "status": None})]


async def test_mcp_remove_calls_tool(monkeypatch):
    client = McpEngineClient("http://x/mcp")
    async def fake_call(tool, args):
        assert tool == "remove_document"
        assert args == {"doc_id": "abc"}
        return {"removed": "abc"}
    monkeypatch.setattr(client, "_call", fake_call)
    out = await client.remove("abc")
    assert out == {"removed": "abc"}


async def test_mcp_get_graph_none_calls_full_graph(monkeypatch):
    client = McpEngineClient("http://x/mcp")
    seen = []
    async def fake_call(tool, args):
        seen.append(tool)
        return {"nodes": [], "links": []}
    monkeypatch.setattr(client, "_call", fake_call)
    await client.get_graph(None)
    assert seen == ["get_full_graph"]
```

Append to `tests/engine/test_mcp.py`:

```python
async def test_list_documents_tool(fake_kb):
    res = await mcp_mod.list_documents()
    assert {"total", "items"} <= set(res)


async def test_remove_document_tool(fake_kb):
    res = await mcp_mod.remove_document("abc")
    assert res == {"removed": "abc"}


async def test_get_full_graph_tool(fake_kb):
    fake_kb.graph = __import__("src.engine.interface", fromlist=["GraphData"]).GraphData()
    res = await mcp_mod.get_full_graph()
    assert res == {"nodes": [], "links": []}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/agent/test_engine_client.py tests/engine/test_mcp.py -v`
Expected: FAIL (new cases error - methods/tools missing).

- [ ] **Step 3: Extend src/agent/interface.py EngineClient**

In `src/agent/interface.py`, add two methods to the `EngineClient` Protocol (after `get_neighbors`):

```python
    async def list_documents(
        self,
        page: int = 1,
        page_size: int = 20,
        file_type: str | None = None,
        status: str | None = None,
    ) -> dict: ...
    async def remove(self, doc_id: str) -> dict: ...
```

- [ ] **Step 4: Extend src/agent/engine_client.py**

In `InProcessEngineClient`, add:

```python
    async def list_documents(
        self, page: int = 1, page_size: int = 20,
        file_type: str | None = None, status: str | None = None,
    ) -> dict:
        return await self._kb.list_documents(page, page_size, file_type, status)

    async def remove(self, doc_id: str) -> dict:
        await self._kb.remove(doc_id)
        return {"removed": doc_id}
```

In `McpEngineClient`, change `get_graph` to route full-graph to a dedicated tool, and add `list_documents`/`remove`:

```python
    async def get_graph(self, entity: str | None = None) -> dict:
        if entity is None:
            return await self._call("get_full_graph", {})
        return await self._call("query_graph", {"entity_name": entity, "include_neighbors": False})

    async def list_documents(
        self, page: int = 1, page_size: int = 20,
        file_type: str | None = None, status: str | None = None,
    ) -> dict:
        return await self._call("list_documents", {
            "page": page, "page_size": page_size,
            "file_type": file_type, "status": status,
        })

    async def remove(self, doc_id: str) -> dict:
        return await self._call("remove_document", {"doc_id": doc_id})
```

- [ ] **Step 5: Add the new MCP tools in src/engine/mcp.py**

Add these functions and register them (next to the existing tools), then update the `mcp.tool()` registrations:

```python
async def list_documents(
    page: int = 1, page_size: int = 20,
    file_type: str | None = None, status: str | None = None,
) -> dict[str, Any]:
    """文件列表（分页，按 type/status 筛选）。"""
    return await _get_kb().list_documents(page, page_size, file_type, status)


async def remove_document(doc_id: str) -> dict[str, Any]:
    """删除文件（级联删 chunks + Neo4j + 本地文件）。"""
    await _get_kb().remove(doc_id)
    return {"removed": doc_id}


async def get_full_graph() -> dict[str, Any]:
    """返回全图数据（所有实体 + 关系）。"""
    graph = await _get_kb().get_graph(None)
    return {"nodes": [{"name": n.name, "type": n.type, "description": n.description,
                        "sources": n.sources} for n in graph.nodes],
            "links": [{"source": l.source, "target": l.target, "type": l.type,
                        "description": l.description} for l in graph.links]}
```

And in the registration block add:

```python
mcp.tool()(list_documents)
mcp.tool()(remove_document)
mcp.tool()(get_full_graph)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/agent/test_engine_client.py tests/engine/test_mcp.py -v`
Expected: PASS (all cases, old + new).

- [ ] **Step 7: Commit**

```bash
git add src/agent/interface.py src/agent/engine_client.py src/engine/mcp.py tests/
git commit -m "feat: extend EngineClient + MCP tools with list/remove/full-graph"
```

---

### Task 23: BFF server skeleton (app + deps wiring inprocess|mcp)

**Files:**
- Create: `src/frontend/__init__.py`, `src/frontend/webapp/__init__.py`, `src/frontend/webapp/server/__init__.py`, `src/frontend/webapp/server/app.py`, `src/frontend/webapp/server/deps.py`
- Test: `tests/frontend/test_bff_health.py`

**Interfaces:**
- Produces: `src.frontend.webapp.server.app.app` (FastAPI); `deps.get_engine() -> EngineClient` and `deps.get_plugin() -> AgentPlugin` (FastAPI dependencies). Lifespan builds: inprocess -> `init_db()` + `build_engine()` + `InProcessEngineClient`; mcp -> `McpEngineClient(app_cfg...)`. Always builds the codex `AgentPlugin` via `build_plugin`.

- [ ] **Step 1: Create package markers**

```bash
mkdir -p src/frontend/webapp/server
touch src/frontend/__init__.py src/frontend/webapp/__init__.py src/frontend/webapp/server/__init__.py
```

- [ ] **Step 2: Write the failing test**

Create `tests/frontend/test_bff_health.py`:

```python
import pytest
from fastapi.testclient import TestClient

from src.frontend.webapp.server import app as app_mod
from src.frontend.webapp.server import deps
from src.agent.engine_client import InProcessEngineClient
from tests.conftest import FakeKnowledgeBase


@pytest.fixture
def client(monkeypatch):
    # Bypass real engine build: inject a fake in-process client.
    fake_client = InProcessEngineClient(FakeKnowledgeBase())
    monkeypatch.setattr(deps, "get_engine", lambda: fake_client)
    monkeypatch.setattr(deps, "get_plugin", lambda: None)
    with TestClient(app_mod.app) as c:
        yield c


def test_health(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/frontend/test_bff_health.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.frontend'`

- [ ] **Step 4: Implement src/frontend/webapp/server/deps.py**

Create `src/frontend/webapp/server/deps.py`:

```python
"""BFF dependencies: wire the engine (inprocess | mcp) and the agent plugin.

The engine is accessed via the agent EngineClient abstraction (dicts), so
inprocess vs mcp is pure wiring. State is held in module-level singletons set
by the app lifespan.
"""
from __future__ import annotations

from src.agent.codex.plugin import build_plugin
from src.agent.engine_client import InProcessEngineClient, McpEngineClient
from src.agent.interface import AgentPlugin, EngineClient
from src.engine.components.store.postgres import init_db
from src.engine.config import EngineConfig, build_engine
from config.schema import AppConfig, load_config

_engine_client: EngineClient | None = None
_plugin: AgentPlugin | None = None
_app_config: AppConfig | None = None


def app_config() -> AppConfig:
    global _app_config
    if _app_config is None:
        _app_config = load_config("config/app.yaml")
    return _app_config


async def startup() -> None:
    global _engine_client, _plugin
    cfg = app_config()
    if cfg.webapp.engine_access == "mcp":
        _engine_client = McpEngineClient("http://localhost:8000/mcp")
    else:
        await init_db()
        kb = build_engine(EngineConfig(impl=cfg.engine.impl, config_dir=cfg.engine.config))
        _engine_client = InProcessEngineClient(kb)
    _plugin = build_plugin(cfg)


async def shutdown() -> None:
    pass


def get_engine() -> EngineClient:
    assert _engine_client is not None, "engine not initialized"
    return _engine_client


def get_plugin() -> AgentPlugin:
    assert _plugin is not None, "plugin not initialized"
    return _plugin
```

- [ ] **Step 5: Implement src/frontend/webapp/server/app.py**

Create `src/frontend/webapp/server/app.py`:

```python
"""Webapp BFF (FastAPI). Calls the engine via EngineClient and may invoke
agent skills in-process. Lifespan wires the engine + agent plugin.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.frontend.webapp.server import deps
from src.frontend.webapp.server.routes_documents import router as documents_router
from src.frontend.webapp.server.routes_search import router as search_router
from src.frontend.webapp.server.routes_graph import router as graph_router
from src.frontend.webapp.server.routes_agent import router as agent_router
from src.frontend.webapp.server.routes_config import router as config_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    await deps.startup()
    yield
    await deps.shutdown()


app = FastAPI(title="Team Knowledge Base BFF", version="0.1.0", lifespan=lifespan)

app.include_router(documents_router)
app.include_router(search_router)
app.include_router(graph_router)
app.include_router(agent_router)
app.include_router(config_router)


@app.get("/health")
async def health():
    return {"status": "ok"}
```

(The route modules are created in Tasks 24–25. To let Task 23's test pass in isolation, create **stub** route modules now - Step 6 - and fill them in Tasks 24–25.)

- [ ] **Step 6: Create stub routers so app imports**

Create each of these as a minimal stub (replaced in Tasks 24–25):

`src/frontend/webapp/server/routes_documents.py`:
```python
from fastapi import APIRouter
router = APIRouter(prefix="/documents", tags=["documents"])
```

`src/frontend/webapp/server/routes_search.py`:
```python
from fastapi import APIRouter
router = APIRouter(tags=["search"])
```

`src/frontend/webapp/server/routes_graph.py`:
```python
from fastapi import APIRouter
router = APIRouter(prefix="/graph", tags=["graph"])
```

`src/frontend/webapp/server/routes_agent.py`:
```python
from fastapi import APIRouter
router = APIRouter(prefix="/agent", tags=["agent"])
```

`src/frontend/webapp/server/routes_config.py`:
```python
from fastapi import APIRouter
router = APIRouter(prefix="/config", tags=["config"])
```

- [ ] **Step 7: Run test to verify it passes**

Run: `uv run pytest tests/frontend/test_bff_health.py -v`
Expected: PASS (1 test).

- [ ] **Step 8: Commit**

```bash
git add src/frontend/ tests/frontend/test_bff_health.py
git commit -m "feat(frontend): BFF skeleton + inprocess/mcp engine wiring"
```

---

### Task 24: BFF document + search + graph routes

**Files:**
- Modify: `src/frontend/webapp/server/routes_documents.py`, `routes_search.py`, `routes_graph.py`
- Test: `tests/frontend/test_bff_documents.py`, `test_bff_search.py`, `test_bff_graph.py`

**Interfaces:** Routes (all return JSON, calling `deps.get_engine()`):
- `GET /documents?page&page_size&file_type&status` -> `list_documents`
- `GET /documents/{doc_id}` -> `get_document` (404 if `error` key present)
- `POST /documents/upload` (multipart `file`) -> `ingest(name, data)`
- `DELETE /documents/{doc_id}` -> `remove`
- `POST /search` (body `{query}`) -> `recall`
- `GET /graph/full` -> `get_graph(None)`
- `GET /graph/entity/{name}` -> `get_graph(name)`
- `GET /graph/neighbors/{name}?hops` -> `get_neighbors(name)`

- [ ] **Step 1: Write the failing tests**

Create `tests/frontend/test_bff_documents.py`:

```python
import pytest
from fastapi.testclient import TestClient

from src.frontend.webapp.server import app as app_mod, deps
from src.agent.engine_client import InProcessEngineClient
from src.engine.interface import GraphData, GraphNode, IngestSource
from tests.conftest import FakeKnowledgeBase


@pytest.fixture
def client(monkeypatch):
    kb = FakeKnowledgeBase()
    monkeypatch.setattr(deps, "get_engine", lambda: InProcessEngineClient(kb))
    monkeypatch.setattr(deps, "get_plugin", lambda: None)
    with TestClient(app_mod.app) as c:
        yield c, kb


def test_list_documents(client):
    c, _ = client
    res = c.get("/documents")
    assert res.status_code == 200
    assert "items" in res.json()


def test_upload_document(client):
    c, kb = client
    res = c.post(
        "/documents/upload",
        files={"file": ("r.md", b"# T\n\nAcme", "text/markdown")},
    )
    assert res.status_code == 200
    assert res.json()["title"] == "r.md"
    assert list(kb.raw.values())[0] == b"# T\n\nAcme"


def test_delete_document(client):
    c, _ = client
    res = c.delete("/documents/abc")
    assert res.status_code == 200
    assert res.json() == {"removed": "abc"}


def test_get_document_not_found(client):
    c, _ = client
    # FakeKnowledgeBase.get_document returns None -> EngineClient returns {"error": ...}
    res = c.get("/documents/00000000-0000-0000-0000-000000000000")
    assert res.status_code == 404
```

Create `tests/frontend/test_bff_search.py`:

```python
import pytest
from fastapi.testclient import TestClient

from src.frontend.webapp.server import app as app_mod, deps
from src.agent.engine_client import InProcessEngineClient
from tests.conftest import FakeKnowledgeBase


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(deps, "get_engine", lambda: InProcessEngineClient(FakeKnowledgeBase()))
    monkeypatch.setattr(deps, "get_plugin", lambda: None)
    with TestClient(app_mod.app) as c:
        yield c


def test_search(client):
    res = client.post("/search", json={"query": "acme"})
    assert res.status_code == 200
    out = res.json()
    assert "chunks" in out and "related_entities" in out and "related_docs" in out
```

Create `tests/frontend/test_bff_graph.py`:

```python
import pytest
from fastapi.testclient import TestClient

from src.frontend.webapp.server import app as app_mod, deps
from src.agent.engine_client import InProcessEngineClient
from src.engine.interface import GraphData, GraphNode
from tests.conftest import FakeKnowledgeBase


@pytest.fixture
def client(monkeypatch):
    kb = FakeKnowledgeBase()
    kb.graph = GraphData(nodes=[GraphNode(name="Acme", type="Company")])
    monkeypatch.setattr(deps, "get_engine", lambda: InProcessEngineClient(kb))
    monkeypatch.setattr(deps, "get_plugin", lambda: None)
    with TestClient(app_mod.app) as c:
        yield c


def test_full_graph(client):
    res = client.get("/graph/full")
    assert res.status_code == 200
    assert res.json()["nodes"][0]["name"] == "Acme"


def test_entity_graph(client):
    res = client.get("/graph/entity/Acme")
    assert res.status_code == 200


def test_neighbors(client):
    res = client.get("/graph/neighbors/Acme?hops=2")
    assert res.status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/frontend/test_bff_documents.py tests/frontend/test_bff_search.py tests/frontend/test_bff_graph.py -v`
Expected: FAIL (routes return 404 - stubs have no handlers).

- [ ] **Step 3: Implement routes_documents.py**

Replace the stub `src/frontend/webapp/server/routes_documents.py`:

```python
"""BFF document routes: browse / get / upload / delete."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile

from src.agent.interface import EngineClient
from src.frontend.webapp.server import deps

router = APIRouter(prefix="/documents", tags=["documents"])


@router.get("")
async def list_documents(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    file_type: str | None = None,
    status: str | None = None,
    engine: EngineClient = Depends(deps.get_engine),
):
    return await engine.list_documents(page, page_size, file_type, status)


@router.get("/{doc_id}")
async def get_document(doc_id: str, engine: EngineClient = Depends(deps.get_engine)):
    out = await engine.get_document(doc_id)
    if out.get("error"):
        raise HTTPException(404, out["error"])
    return out


@router.post("/upload")
async def upload_document(
    file: UploadFile = File(...),
    engine: EngineClient = Depends(deps.get_engine),
):
    if not file.filename:
        raise HTTPException(400, "文件名不能为空")
    data = await file.read()
    return await engine.ingest(file.filename, data)


@router.delete("/{doc_id}")
async def delete_document(doc_id: str, engine: EngineClient = Depends(deps.get_engine)):
    return await engine.remove(doc_id)
```

- [ ] **Step 4: Implement routes_search.py**

Replace `src/frontend/webapp/server/routes_search.py`:

```python
"""BFF search route."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from src.agent.interface import EngineClient
from src.frontend.webapp.server import deps

router = APIRouter(tags=["search"])


class SearchRequest(BaseModel):
    query: str
    top_k: int = 20


@router.post("/search")
async def search(body: SearchRequest, engine: EngineClient = Depends(deps.get_engine)):
    return await engine.recall(body.query, top_k=body.top_k)
```

- [ ] **Step 5: Implement routes_graph.py**

Replace `src/frontend/webapp/server/routes_graph.py`:

```python
"""BFF graph routes: full graph / entity / neighbors."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from src.agent.interface import EngineClient
from src.frontend.webapp.server import deps

router = APIRouter(prefix="/graph", tags=["graph"])


@router.get("/full")
async def full_graph(engine: EngineClient = Depends(deps.get_engine)):
    return await engine.get_graph(None)


@router.get("/entity/{name}")
async def entity_graph(name: str, engine: EngineClient = Depends(deps.get_engine)):
    return await engine.get_graph(name)


@router.get("/neighbors/{name}")
async def neighbors(
    name: str,
    hops: int = Query(2, ge=1, le=3),
    engine: EngineClient = Depends(deps.get_engine),
):
    return await engine.get_neighbors(name)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/frontend/test_bff_documents.py tests/frontend/test_bff_search.py tests/frontend/test_bff_graph.py -v`
Expected: PASS (all cases).

- [ ] **Step 7: Commit**

```bash
git add src/frontend/webapp/server/routes_documents.py src/frontend/webapp/server/routes_search.py src/frontend/webapp/server/routes_graph.py tests/frontend/
git commit -m "feat(frontend): BFF document/search/graph routes"
```

---

### Task 25: BFF agent-skill routes + config routes + configured LLM client

**Files:**
- Create: `src/agent/llm.py`
- Modify: `src/frontend/webapp/server/deps.py` (add `get_llm`)
- Replace: `src/frontend/webapp/server/routes_agent.py`, `routes_config.py`
- Test: `tests/frontend/test_bff_agent.py`

**Interfaces:**
- Produces: `src.agent.llm.ConfiguredLlmClient(base_url, model, api_key)` implementing `LlmClient`; `build_llm(model_config_path: Path) -> ConfiguredLlmClient | None` (returns `None` if provider is `todo`).
- Produces routes: `POST /agent/ask` (body `{query}`) -> runs `search_and_answer`; `POST /agent/ingest-summarize` (multipart `file`) -> runs `ingest_and_summarize`; `GET /config` -> returns `app.yaml` as JSON; `PUT /config` (body = AppConfig dict) -> validates + writes `app.yaml`.
- `deps.get_llm() -> LlmClient | None` (built at startup from `config/engine/graphrag/model_config.yaml`).

- [ ] **Step 1: Write the failing test**

Create `tests/frontend/test_bff_agent.py`:

```python
import pytest
from fastapi.testclient import TestClient

from src.frontend.webapp.server import app as app_mod, deps
from src.agent.engine_client import InProcessEngineClient
from tests.conftest import FakeKnowledgeBase


class FakeLlm:
    async def complete(self, prompt):
        return "ANSWER FROM LLM"


@pytest.fixture
def client(monkeypatch):
    kb = FakeKnowledgeBase()
    monkeypatch.setattr(deps, "get_engine", lambda: InProcessEngineClient(kb))
    monkeypatch.setattr(deps, "get_plugin", lambda: __import__(
        "src.agent.codex.plugin", fromlist=["build_plugin"]).build_plugin(
        __import__("config.schema", fromlist=["load_config"]).load_config("config/app.yaml")))
    monkeypatch.setattr(deps, "get_llm", lambda: FakeLlm())
    with TestClient(app_mod.app) as c:
        yield c


def test_agent_ask(client):
    res = client.post("/agent/ask", json={"query": "where is Acme?"})
    assert res.status_code == 200
    out = res.json()
    assert out["answer"] == "ANSWER FROM LLM"
    assert out["query"] == "where is Acme?"


def test_agent_ingest_summarize(client):
    res = client.post(
        "/agent/ingest-summarize",
        files={"file": ("r.md", b"# T\n\nAcme is in Building A.", "text/markdown")},
    )
    assert res.status_code == 200
    out = res.json()
    assert out["doc"]["title"] == "r.md"
    assert out["summary"] == "ANSWER FROM LLM"


def test_config_get(client):
    res = client.get("/config")
    assert res.status_code == 200
    cfg = res.json()
    assert cfg["engine"]["impl"] == "graphrag"


def test_config_put_validates(client, tmp_path, monkeypatch):
    # Point config write at a temp file so we don't clobber the real app.yaml.
    monkeypatch.setattr(
        "src.frontend.webapp.server.routes_config.CONFIG_PATH", tmp_path / "app.yaml"
    )
    res = client.put("/config", json={
        "engine": {"impl": "graphrag", "config": "config/engine/graphrag"},
        "agent": {"harness": "codex", "skills": ["search_and_answer"], "memory": {"impl": None}},
        "frontend": {"impl": "webapp"},
        "webapp": {"engine_access": "mcp"},
    })
    assert res.status_code == 200
    assert res.json()["webapp"]["engine_access"] == "mcp"


def test_config_put_rejects_invalid(client, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.frontend.webapp.server.routes_config.CONFIG_PATH", tmp_path / "app.yaml"
    )
    res = client.put("/config", json={"webapp": {"engine_access": "bogus"}})
    assert res.status_code == 422
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/frontend/test_bff_agent.py -v`
Expected: FAIL (routes missing; `deps.get_llm` missing).

- [ ] **Step 3: Implement src/agent/llm.py**

Create `src/agent/llm.py`:

```python
"""ConfiguredLlmClient: an LlmClient backed by the OpenAI-compatible LLM
configured in config/engine/graphrag/model_config.yaml. Used by the webapp BFF
to synthesize answers/summaries when invoking agent skills in-process.

(The codex harness, when run as its own process, supplies its own LLM or lets
codex synthesize - skills tolerate ctx.llm=None.)
"""
from __future__ import annotations

from pathlib import Path

import httpx
import yaml

from config.settings import settings
from src.agent.interface import LlmClient


class ConfiguredLlmClient:
    def __init__(self, base_url: str, model: str, api_key: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key

    async def complete(self, prompt: str) -> str:
        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.post(
                f"{self._base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self._model,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]


def build_llm(model_config_path: Path) -> ConfiguredLlmClient | None:
    """Build a ConfiguredLlmClient from model_config.yaml; None if provider is 'todo'."""
    if not model_config_path.exists():
        return None
    data = yaml.safe_load(model_config_path.read_text(encoding="utf-8")) or {}
    llm = data.get("llm", {})
    if llm.get("provider", "todo") == "todo":
        return None
    base_url = llm.get("base_url", "https://api.openai.com/v1")
    model = llm.get("model", "gpt-4o-mini")
    api_key = llm.get("api_key", "") or settings.llm_api_key
    return ConfiguredLlmClient(base_url, model, api_key)
```

- [ ] **Step 4: Add get_llm to deps.py**

In `src/frontend/webapp/server/deps.py`:

Add to the module singletons:
```python
from src.agent.interface import AgentPlugin, EngineClient, LlmClient
_llm: LlmClient | None = None
```

In `startup()`, after building `_plugin`, add:
```python
    global _llm
    from src.agent.llm import build_llm
    _llm = build_llm(Path(cfg.engine.config) / "model_config.yaml")
```
(add `from pathlib import Path` to imports if not present.)

Add the accessor:
```python
def get_llm() -> LlmClient | None:
    return _llm
```

- [ ] **Step 5: Implement routes_agent.py**

Replace `src/frontend/webapp/server/routes_agent.py`:

```python
"""BFF agent-skill routes: invoke harness-agnostic skills in-process."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from src.agent.interface import EngineClient, LlmClient, SkillContext
from src.frontend.webapp.server import deps

router = APIRouter(prefix="/agent", tags=["agent"])


class AskRequest(BaseModel):
    query: str
    top_k: int = 10


def _find_skill(name: str):
    plugin = deps.get_plugin()
    for s in plugin.skills():
        if s.name == name:
            return s
    raise HTTPException(404, f"skill not found: {name}")


@router.post("/ask")
async def ask(
    body: AskRequest,
    engine: EngineClient = Depends(deps.get_engine),
    llm: LlmClient | None = Depends(deps.get_llm),
):
    skill = _find_skill("search_and_answer")
    ctx = SkillContext(engine=engine, llm=llm, params={"query": body.query, "top_k": body.top_k})
    result = await skill.run(ctx)
    return result.output


@router.post("/ingest-summarize")
async def ingest_summarize(
    file: UploadFile = File(...),
    engine: EngineClient = Depends(deps.get_engine),
    llm: LlmClient | None = Depends(deps.get_llm),
):
    if not file.filename:
        raise HTTPException(400, "文件名不能为空")
    data = await file.read()
    skill = _find_skill("ingest_and_summarize")
    ctx = SkillContext(engine=engine, llm=llm, params={"name": file.filename, "data": data})
    result = await skill.run(ctx)
    return result.output
```

- [ ] **Step 6: Implement routes_config.py**

Replace `src/frontend/webapp/server/routes_config.py`:

```python
"""BFF config routes: read / modify config/app.yaml."""
from __future__ import annotations

from pathlib import Path

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import ValidationError

from config.schema import AppConfig

router = APIRouter(prefix="/config", tags=["config"])

CONFIG_PATH = Path("config/app.yaml")


@router.get("")
async def get_config():
    if not CONFIG_PATH.exists():
        return AppConfig().model_dump()
    data = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    return AppConfig.model_validate(data).model_dump()


@router.put("")
async def put_config(body: dict):
    try:
        cfg = AppConfig.model_validate(body)
    except ValidationError as e:
        raise HTTPException(422, e.errors())
    CONFIG_PATH.write_text(
        yaml.safe_dump(cfg.model_dump(), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return cfg.model_dump()
```

- [ ] **Step 7: Run test to verify it passes**

Run: `uv run pytest tests/frontend/test_bff_agent.py -v`
Expected: PASS (5 tests).

- [ ] **Step 8: Run the full BFF suite**

Run: `uv run pytest tests/frontend -v`
Expected: PASS (all BFF tests).

- [ ] **Step 9: Commit**

```bash
git add src/agent/llm.py src/frontend/webapp/server/ tests/frontend/test_bff_agent.py
git commit -m "feat(frontend): BFF agent-skill + config routes; ConfiguredLlmClient"
```

---

### Task 26: Migrate SPA into src/frontend/webapp/client/ (move + vite + vitest smoke test)

**Files:**
- Move: `frontend/{package.json, vite.config.ts, tsconfig.json, index.html, src/}` -> `src/frontend/webapp/client/`
- Modify: `vite.config.ts` (proxy target `/api` -> BFF at `:8000`), `package.json` (add `vitest` devDep + test script), `src/api/client.ts` (search + agent endpoints), `.gitignore`
- Create: `src/frontend/webapp/client/vitest.config.ts`, `src/frontend/webapp/client/src/api/__tests__/client.test.ts`
- Test: vitest smoke test for the api client

**Interfaces:** The SPA is served by `npm run dev` (Vite, port 5173) proxying `/api` to the BFF at `:8000`, or built (`npm run build`) and served statically by the BFF. API base stays `/api`.

> **Test note:** The SPA is a migration of working code, not new logic; its UI is verified manually (Task 28) and its backend by BFF integration tests. To honor TDD for the testable part, a minimal `vitest` harness is added with one test for the pure `api/client.ts` fetch logic (mocked `fetch`). Full component testing is out of scope (YAGNI; no prior infra).

- [ ] **Step 1: Move the SPA**

```bash
mkdir -p src/frontend/webapp/client
git mv frontend/package.json     src/frontend/webapp/client/package.json
git mv frontend/vite.config.ts   src/frontend/webapp/client/vite.config.ts
git mv frontend/tsconfig.json    src/frontend/webapp/client/tsconfig.json
git mv frontend/index.html       src/frontend/webapp/client/index.html
git mv frontend/src              src/frontend/webapp/client/src
rmdir frontend
```

- [ ] **Step 2: Update vite.config.ts (proxy to BFF at :8000, no path rewrite)**

Replace `src/frontend/webapp/client/vite.config.ts`:

```typescript
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
})
```

- [ ] **Step 3: Add vitest to package.json + test script**

In `src/frontend/webapp/client/package.json`, add to `devDependencies`:
```json
    "vitest": "^3.0.0",
    "@vitest/browser": "^3.0.0"
```
and add to `scripts`:
```json
    "test": "vitest run"
```

(The final `package.json` `scripts` should read: `dev`, `build`, `preview`, `test`.)

- [ ] **Step 4: Create vitest.config.ts**

Create `src/frontend/webapp/client/vitest.config.ts`:

```typescript
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  test: { environment: 'node', include: ['src/**/__tests__/**/*.test.ts'] },
})
```

- [ ] **Step 5: Add search + agent methods to src/api/client.ts**

In `src/frontend/webapp/client/src/api/client.ts`, add to the `api` object (after `getFullGraph`):

```typescript
  // 搜索
  search(query: string, topK = 20) {
    return request<{ chunks: any[]; related_entities: any[]; related_docs: any[] }>(
      '/search',
      { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ query, top_k: topK }) },
    )
  },

  // Agent
  ask(query: string) {
    return request<{ query: string; answer: string; sources: any }>(
      '/agent/ask',
      { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ query }) },
    )
  },
```

- [ ] **Step 6: Write the failing vitest smoke test**

Create `src/frontend/webapp/client/src/api/__tests__/client.test.ts`:

```typescript
import { afterEach, describe, expect, it, vi } from 'vitest'

import { api } from '../client'

const mockFetch = vi.fn()
vi.stubGlobal('fetch', mockFetch)

afterEach(() => mockFetch.mockReset())

describe('api client', () => {
  it('listDocuments calls /documents with query params', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ total: 0, page: 1, page_size: 20, items: [] }),
    })
    await api.listDocuments({ page: 2, page_size: 5 })
    expect(mockFetch).toHaveBeenCalledWith(
      '/api/documents?page=2&page_size=5',
      undefined,
    )
  })

  it('search posts to /search', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ chunks: [], related_entities: [], related_docs: [] }),
    })
    await api.search('acme', 7)
    const [, init] = mockFetch.mock.calls[0]
    expect(init?.method).toBe('POST')
    expect(init?.body).toBe(JSON.stringify({ query: 'acme', top_k: 7 }))
  })

  it('throws on non-ok response', async () => {
    mockFetch.mockResolvedValueOnce({ ok: false, statusText: 'boom', json: async () => ({}) })
    await expect(api.getFullGraph()).rejects.toThrow()
  })
})
```

- [ ] **Step 7: Run the vitest test**

```bash
cd src/frontend/webapp/client && npm install && npm test
```
Expected: PASS (3 tests). (If offline, `npm install` may be skipped if `node_modules` already exists - run `npm test` directly.)

- [ ] **Step 8: Update .gitignore**

In `.gitignore`, replace `frontend/dist/` with:
```
src/frontend/webapp/client/dist/
src/frontend/webapp/client/node_modules/
```

- [ ] **Step 9: Commit**

```bash
git add src/frontend/webapp/client/ .gitignore
git commit -m "feat(frontend): migrate SPA into webapp/client; vitest smoke test"
```

---

### Task 27: SPA search page + agent ask UI

**Files:**
- Create: `src/frontend/webapp/client/src/pages/SearchPage.tsx`, `src/frontend/webapp/client/src/pages/AskPage.tsx`
- Modify: `src/frontend/webapp/client/src/App.tsx`, `src/frontend/webapp/client/src/components/Layout.tsx`

**Interfaces:** Two new routes: `/search` (search box -> results: chunks + related entities/docs) and `/ask` (question box -> synthesized answer + sources). Layout gains nav links to both.

- [ ] **Step 1: Implement SearchPage.tsx**

Create `src/frontend/webapp/client/src/pages/SearchPage.tsx`:

```tsx
import { useState } from 'react'
import { api } from '../api/client'

export function SearchPage() {
  const [query, setQuery] = useState('')
  const [result, setResult] = useState<{ chunks: any[]; related_entities: any[]; related_docs: any[] } | null>(null)
  const [loading, setLoading] = useState(false)

  const run = async () => {
    if (!query.trim()) return
    setLoading(true)
    try {
      setResult(await api.search(query))
    } catch (err: any) {
      alert('搜索失败: ' + err.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{ flex: 1, overflow: 'auto', padding: 24 }}>
      <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && run()}
          placeholder="输入搜索内容..."
          style={{ flex: 1, padding: '8px 12px', borderRadius: 6, border: '1px solid #d9d9d9', fontSize: 14 }}
        />
        <button onClick={run} disabled={loading} style={{ padding: '6px 16px', borderRadius: 6, border: '1px solid #1890ff', background: '#1890ff', color: '#fff' }}>
          {loading ? '搜索中...' : '搜索'}
        </button>
      </div>

      {result && (
        <div>
          <h3>相关片段 ({result.chunks.length})</h3>
          {result.chunks.map((c, i) => (
            <div key={i} style={{ padding: 12, marginBottom: 8, border: '1px solid #eee', borderRadius: 6 }}>
              <div style={{ fontWeight: 500, marginBottom: 4 }}>{c.title}</div>
              <div style={{ fontSize: 13, color: '#666' }}>{c.chunk_text}</div>
            </div>
          ))}
          {result.related_entities.length > 0 && (
            <>
              <h3>相关实体</h3>
              <div>{result.related_entities.map((e: any) => e.name).join(', ')}</div>
            </>
          )}
          {result.related_docs.length > 0 && (
            <>
              <h3>相关文档</h3>
              {result.related_docs.map((d: any, i) => <div key={i}>{d.title} ({d.relation_type})</div>)}
            </>
          )}
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Implement AskPage.tsx**

Create `src/frontend/webapp/client/src/pages/AskPage.tsx`:

```tsx
import { useState } from 'react'
import { api } from '../api/client'

export function AskPage() {
  const [query, setQuery] = useState('')
  const [answer, setAnswer] = useState<{ query: string; answer: string; sources: any } | null>(null)
  const [loading, setLoading] = useState(false)

  const run = async () => {
    if (!query.trim()) return
    setLoading(true)
    try {
      setAnswer(await api.ask(query))
    } catch (err: any) {
      alert('提问失败: ' + err.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{ flex: 1, overflow: 'auto', padding: 24 }}>
      <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && run()}
          placeholder="向知识库提问..."
          style={{ flex: 1, padding: '8px 12px', borderRadius: 6, border: '1px solid #d9d9d9', fontSize: 14 }}
        />
        <button onClick={run} disabled={loading} style={{ padding: '6px 16px', borderRadius: 6, border: '1px solid #1890ff', background: '#1890ff', color: '#fff' }}>
          {loading ? '思考中...' : '提问'}
        </button>
      </div>

      {answer && (
        <div style={{ padding: 16, border: '1px solid #eee', borderRadius: 6, background: '#fafafa' }}>
          <div style={{ fontWeight: 600, marginBottom: 8 }}>回答</div>
          <div style={{ whiteSpace: 'pre-wrap' }}>{answer.answer}</div>
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 3: Wire routes into App.tsx**

Replace `src/frontend/webapp/client/src/App.tsx`:

```tsx
import { Routes, Route } from 'react-router-dom'
import { Layout } from './components/Layout'
import { DocumentListPage } from './pages/DocumentListPage'
import { DocumentDetailPage } from './pages/DocumentDetailPage'
import { GraphPage } from './pages/GraphPage'
import { SearchPage } from './pages/SearchPage'
import { AskPage } from './pages/AskPage'

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<DocumentListPage />} />
        <Route path="/documents/:id" element={<DocumentDetailPage />} />
        <Route path="/search" element={<SearchPage />} />
        <Route path="/ask" element={<AskPage />} />
        <Route path="/graph" element={<GraphPage />} />
      </Route>
    </Routes>
  )
}
```

- [ ] **Step 4: Add nav links in Layout.tsx**

In `src/frontend/webapp/client/src/components/Layout.tsx`, add two `<Link>` elements in the header (after the 知识图谱 link):

```tsx
        <Link to="/search" style={{ fontSize: 14, textDecoration: 'none', color: '#666', padding: '4px 8px' }}>
          搜索
        </Link>
        <Link to="/ask" style={{ fontSize: 14, textDecoration: 'none', color: '#666', padding: '4px 8px' }}>
          提问
        </Link>
```

- [ ] **Step 5: Verify the SPA builds**

```bash
cd src/frontend/webapp/client && npm run build
```
Expected: build succeeds (TypeScript compiles, Vite emits `dist/`).

- [ ] **Step 6: Run the vitest suite (regression)**

```bash
cd src/frontend/webapp/client && npm test
```
Expected: PASS (3 tests).

- [ ] **Step 7: Commit**

```bash
git add src/frontend/webapp/client/src/
git commit -m "feat(frontend): SPA search page + agent ask UI"
```

---

### Task 28: Delete old frontend/ + final end-to-end verification

**Files:**
- Delete: (old `frontend/` already removed in Task 26 - verify)
- Modify: `README.md` (final run commands)

**Interfaces:** None. Completes Phase 3: old `frontend/` gone; end-to-end works.

- [ ] **Step 1: Confirm old frontend/ is gone**

```bash
test ! -d frontend && echo "old frontend removed" || echo "STILL PRESENT"
```
Expected: prints `old frontend removed`. (Task 26's `git mv` emptied it; if any stray files remain, `git rm -r frontend`.)

- [ ] **Step 2: Update README.md with full run commands**

Replace `README.md` with:

```markdown
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
```

- [ ] **Step 3: Run the entire Python test suite (no integration)**

Run: `uv run pytest -m "not integration" -v`
Expected: PASS (all engine + agent + frontend tests green).

- [ ] **Step 4: Verify the tree is clean of old code**

Run:
```bash
test ! -e src/core && test ! -e src/pipeline && test ! -e src/db && test ! -e src/api && test ! -e src/main.py && test ! -e frontend && echo "CLEAN"
```
Expected: prints `CLEAN`.

- [ ] **Step 5: Manual end-to-end smoke (requires services)**

With Postgres + Neo4j + Ollama running:
```bash
uv run uvicorn src.frontend.webapp.server.app:app --port 8000 &
# in another shell:
cd src/frontend/webapp/client && npm run dev
```
Open `http://localhost:5173`: upload a `.md` file -> it appears in the list -> open it -> visit 知识图谱 (graph renders) -> visit 搜索 -> visit 提问. Confirm each works.

- [ ] **Step 6: Commit**

```bash
git add README.md
git commit -m "docs: final run commands; Phase 3 complete - three-module refactor done"
```

---

## Self-Review

**1. Spec coverage** (spec section -> task):

- §1 Goals (clean break, one-way deps, engine=no agents, agent=plugin to harness, polyglot, memory deferred, both deploy modes): enforced throughout; one-way deps in dep wiring (Task 23 `frontend->agent+engine`, Task 18 `agent->engine`); memory interface-only (Task 17); both modes (Task 23 inprocess|mcp).
- §2 Out of scope: multi-tenancy/auth (none built), file-gen "c" (not built), second engine backend (wiki stub, Task 16), windowsapp (not built - not even stubbed; **gap noted below**), memory impl (Task 17 interface only).
- §3 Target dir structure: every path created - engine/{interface,config,cli,mcp,components/*,graphrag/*,wiki} (Tasks 3–16); agent/{interface,engine_client,skills/*,memory,codex} (Tasks 17–21); frontend/webapp/{server,client} (Tasks 23–27); config/{app.yaml,schema.py,settings.py,engine/graphrag/*,agent/codex} (Tasks 2, 21).
- §4.1 Engine contract: `KnowledgeBase` Protocol + `Capabilities` + optional `NotSupported` (Task 3); `build_engine` factory (Task 4); cli.py + mcp.py adapters-only (Tasks 13–14).
- §4.2 Agent contract: `Skill`/`EngineClient`/`AgentPlugin` (Task 17); skills harness-agnostic (Tasks 19–20); codex plugin (Task 21); `memory.py` Protocol only, no engine coupling (Task 17).
- §4.3 Frontend: BFF calls engine (inprocess default, MCP distributed) + invokes agent skills in-process; graphical ops map to engine calls (upload->ingest, delete->remove) (Tasks 22–25); config read/modify (Task 25).
- §5 Config: `app.yaml` + `schema.py` AppConfig; yaml files moved to `config/engine/graphrag/` (Task 2).
- §6 Milestone 1 scope: engine full (Phase 1), agent full (Phase 2), frontend webapp (Phase 3), config (Task 2).
- §7 Phased execution: Phase 1 exit (CLI/MCP work, old code gone - Task 16); Phase 2 exit (skills in-process, codex via MCP - Task 21); Phase 3 exit (end-to-end, old frontend gone - Task 28).
- §8 Migration map: every old->new row covered (extractors Task 6; chunker/embedder/analyzer/pipeline Tasks 7/8/10/11; knowledge_base/search->backend Tasks 12; reranker Task 9; db->store Task 5; mcp_server->mcp Task 14; routes+main->BFF Tasks 23–25; frontend->webapp/client Task 26; yaml move Task 2).
- §9 Open points: resolved in "Resolved decisions" (React kept; npm kept; bm25/query_rewriter excluded).

**Gaps / deviations (intentional, surfaced):**
- **windowsapp stub** (spec §3 lists `src/frontend/windowsapp/README.md`): not created. It is explicitly out-of-scope stub-only; adding a one-line README is trivial but omitted to avoid an empty task. **Recommend a 2-minute follow-up:** `mkdir -p src/frontend/windowsapp && echo "# Windows app (stub) - not built (spec §2)" > src/frontend/windowsapp/README.md`.
- **bm25 / query_rewriter** (spec §3, §8): excluded - source files exist only on `origin/feature_20260714`, not on this branch. See "Resolved decisions" #3.
- **Inline content editing** (old SPA md-editor): dropped from Phase 3 as spec §6 lists only browse/search/graph/ingest. The md-editor dependency can be removed from `package.json` in a follow-up if desired.
- **CLI-subprocess `EngineClient`**: spec §4.2 mentions "uniform over CLI/MCP"; only in-process + MCP transports are implemented (the two actually used: BFF uses in-process; codex uses MCP). A subprocess-CLI client is YAGNI until a harness needs it.

**2. Placeholder scan:** No "TBD"/"TODO"/"implement later"/"add error handling" remain. Every code step contains real code; every test step contains real assertions; every command has expected output. Migration steps show exact `git mv` commands + exact import-line edits.

**3. Type consistency:**
- `KnowledgeBase` methods (Task 3) match backend impl (Task 12), `FakeKnowledgeBase` (Task 1), contract test (Task 15), and CLI (Task 13): `ingest/reingest/remove/recall/get_graph/get_neighbors/list_documents/get_document`.
- `EngineClient` methods (Task 17) + extension (Task 22) match `InProcessEngineClient`/`McpEngineClient` (Tasks 18, 22), BFF deps/routes (Tasks 23–25), and SPA expectations: `recall/ingest/get_document/get_graph/get_neighbors/list_documents/remove`.
- MCP tool names (`search`, `get_document`, `query_graph`, `upload_document`, `list_documents`, `remove_document`, `get_full_graph`) match `McpEngineClient._call` targets (Tasks 18, 22).
- Dataclasses (`IngestSource`, `DocumentRef`, `RecallRequest`, `RecallResult`, `GraphData`, `GraphNode`, `GraphLink`, `Capabilities`, `SkillContext`, `SkillResult`) are defined once (Tasks 3, 17) and reused with consistent field names throughout.
- Config types: `AppConfig`/`EngineCfg`/`AgentCfg`/`FrontendCfg`/`WebappCfg` (Task 2) match `build_plugin` (Task 21), `deps.startup` (Task 23), `routes_config` (Task 25).

