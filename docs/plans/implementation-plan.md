# Team Knowledge Base Implementation Plan

> **参考设计文档:** `docs/specs/design.md`

**Goal:** 构建园区团队知识库系统，支持多模态文件存储、三层漏斗语义检索、知识图谱、Web UI 和 MCP 接口。

**Architecture:** 单进程 FastAPI Monolith，KnowledgeBase 逻辑层被 REST API、MCP Server、Pipeline 共享。Postgres+pgvector 存储/检索，Neo4j 知识图谱，React 前端。

**Tech Stack:** Python 3.12+ / uv / FastAPI / Postgres 16 + pgvector / Neo4j 5 / Ollama (nomic-embed-text) / React + Vite / Python MCP SDK

## Global Constraints

- 项目根目录: `/Users/caoyurui/study/team-knowledge-base`
- Python 环境: `uv`（不用 pip/poetry）
- Postgres: 端口 5433（避免与现有 crewstudio-postgres 5432 冲突）
- Neo4j: 复用现有 Docker 容器 `neo4j-graphiti`（端口 7474/7687）
- Embedding 模型: `nomic-embed-text` via Ollama (768d)
- LLM 和守门模型: 占位配置，用户后续提供
- 向量维度: 768（必须匹配 embedding 模型）
- 所有模型配置在 `config/model_config.yaml`，热加载
- 实体/关系类型在 `config/entity_schema.yaml`，热加载
- MVP 无权限控制
- 文件存储: 本地 `uploads/` 目录

## 现有环境信息（重要！）

- **Postgres**: 需新建 `pgvector/pgvector:pg16` 容器，端口 5433
- **Neo4j**: 已有容器 `neo4j-graphiti`（neo4j:5），端口 7474/7687
- **Ollama**: 本地运行，已有 `nomic-embed-text` 模型（768维）
- **crewstudio-postgres**: 已有容器 postgres:16-alpine，端口 5432，user=crewstudio，db=crewstudio，**绝对不要动**
- **Redis**: 已有容器 crewstudio-redis，端口 6379
- **Qdrant**: 已有容器 crewstudio-qdrant，端口 6333

---

## Phase 1: 项目骨架 + 基础设施

### Task 1: 项目初始化 + Docker

**产出:** uv 项目、docker-compose、配置文件、health endpoint 可运行

**步骤:**
1. `uv init --name team-knowledge-base --python 3.12`
2. `uv add` 所有依赖
3. 创建 `docker-compose.yml`（pgvector/pgvector:pg16，端口 5433）
4. 创建 `.env.example` + `.env`
5. 创建 `config/entity_schema.yaml`（园区实体/关系类型，详见 design.md）
6. 创建 `config/model_config.yaml`（模型配置，详见 design.md）
7. 创建 `src/main.py`（FastAPI app + /health endpoint）
8. 创建目录结构
9. 创建 `.gitignore`
10. `docker compose up -d` + 验证 Postgres 健康
11. `uv run uvicorn src.main:app` + curl /health 验证
12. `git init` + 首次提交

**Python 依赖:**
```
fastapi uvicorn[standard] sqlalchemy[asyncio] asyncpg pgvector
python-multipart pydantic pydantic-settings python-dotenv pyyaml httpx
neo4j
pypdf python-docx python-pptx pytesseract
numpy
mcp
pytest pytest-asyncio ruff
```

---

### Task 2: 数据库层 (Postgres + Neo4j)

**产出:** ORM 模型、DB 连接、schema 初始化、Neo4j 客户端

**文件:**
- `src/db/config.py` — Settings（从 .env 读取）
- `src/db/models.py` — SQLAlchemy 模型（Document, Chunk）
- `src/db/postgres.py` — 异步引擎 + init_db() + session
- `src/db/neo4j_client.py` — Neo4j 异步客户端

**Document 表:** id(UUID), title, file_type, raw_text, overview, file_path, content_hash, status(pending/processing/indexed/failed), error_msg, created_at, updated_at
**Chunk 表:** id(UUID), doc_id(FK→documents), chunk_index, chunk_text, embedding(vector(768)), overview, doc_uri, token_count, created_at
**HNSW 索引:** `idx_chunks_embedding ON chunks USING hnsw (embedding vector_cosine_ops)`
**init_db():** 启动时自动 CREATE EXTENSION + CREATE TABLE + CREATE INDEX
**Neo4jClient:** upsert_document_node, upsert_entity, create_relation, query_neighbors, delete_document_graph

---

## Phase 2: Pipeline（文件入库管道）

### Task 3: 文件提取器（策略模式）

**文件:**
- `src/pipeline/extractors/base.py` — BaseExtractor 抽象类
- `src/pipeline/extractors/markdown.py` — MarkdownExtractor (md, txt)
- `src/pipeline/extractors/pdf.py` — PDFExtractor (pypdf)
- `src/pipeline/extractors/docx.py` — DocxExtractor (python-docx)
- `src/pipeline/extractors/pptx.py` — PPTXExtractor (python-pptx)
- `src/pipeline/extractors/image.py` — ImageExtractor (pytesseract OCR)
- `src/pipeline/extractors/registry.py` — ExtractorRegistry（按 file_type 路由）

**接口:** `BaseExtractor.extract(file_path) -> str`

---

### Task 4: 文本分块器

**文件:** `src/pipeline/chunker.py`
**逻辑:** 语义段落切分，~500 tokens/chunk，~50 tokens 重叠
**接口:** `chunk_text(text, chunk_size=500, overlap=50) -> list[Chunk]`

---

### Task 5: Embedding 服务

**文件:** `src/pipeline/embedder.py`
**逻辑:** 通过 Ollama HTTP API 调用 nomic-embed-text
**接口:** `Embedder.embed_text(text) -> list[float]`, `Embedder.embed_batch(texts) -> list[list[float]]`

---

### Task 6: LLM 分析器

**文件:** `src/pipeline/analyzer.py`
**逻辑:** 单次 LLM 调用 → overview + entities + relations + file_relations
**Prompt:** 从 entity_schema.yaml 动态生成，预设核心园区类型 + LLM 开放补充
**接口:** `Analyzer.analyze(text, title) -> AnalysisResult`

---

### Task 7: Pipeline 编排

**文件:** `src/pipeline/pipeline.py`
**流程:** status→processing → 提取 → LLM分析 → chunking → embedding → 写Postgres → 写Neo4j → status→indexed
**异常处理:** status→failed + error_msg
**幂等性:** content_hash 判断，未变则跳过
**接口:** `Pipeline.process_file(doc_id, file_path, title, file_type)`, `Pipeline.reindex_document(doc_id, new_text)`

---

## Phase 3: 核心业务逻辑 + API

### Task 8: KnowledgeBase 核心层

**文件:** `src/core/knowledge_base.py`
**接口:** upload_file, edit_content, get_document, list_documents, delete_document, search, get_entity, get_neighbors

### Task 9: 三层漏斗检索

**文件:** `src/core/search.py`
**第一层:** query embed → pgvector cosine → Top-20
**第二层:** overview embed (gatekeeper) → cosine ≥ 0.7 → keep
**第三层:** 获取原文 + Neo4j 1-2跳 → LLM 合成答案

### Task 10: REST API

**文件:** `src/api/routes.py`
**端点:** POST /documents/upload, PUT /documents/{id}/content, GET /documents/{id}, GET /documents, DELETE /documents/{id}, POST /search, GET /graph/entity/{name}, GET /graph/neighbors/{id}

### Task 11: MCP Server

**文件:** `src/api/mcp_server.py`
**Tools:** search, get_document, query_graph, upload_document
**传输:** streamable HTTP

---

## Phase 4: 前端

### Task 12: React 前端

**技术:** React + Vite + @uiw/react-md-editor
**页面:** 搜索页(答案+引用+实体) + 文件列表(树+筛选) + 文件编辑(md编辑器) + 状态指示

---

## Phase 5: 集成

### Task 13: 端到端验证

1. 上传 md → status pending→processing→indexed
2. 搜索 → 三层漏斗返回正确结果
3. 编辑 → re-index 触发
4. Neo4j 实体/关系验证
5. MCP tool 调用验证
