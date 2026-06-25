# 园区团队知识库系统设计文档

## 概述

为园区运营团队设计的知识管理系统，支持多模态文件存储、高精度语义检索（三层漏斗）、知识图谱（Neo4j），并提供 Web 界面（浏览/编辑/搜索）和 MCP 接口（Agent 接入）。

## 架构选型

**方案 A：单进程 Monolith**（已确认）

```
┌─────────────────────────────────────────────┐
│              FastAPI 单进程                  │
│  ┌──────────┐ ┌──────────┐ ┌─────────────┐  │
│  │ REST API │ │MCP Server│ │ Pipeline    │  │
│  │ (Web用)  │ │ (Agent用)│ │ (异步任务)   │  │
│  └────┬─────┘ └────┬─────┘ └──────┬──────┘  │
│       └─────────────┴──────────────┘         │
│            共享业务逻辑层 (KnowledgeBase)      │
└──────────────────┬───────────────────────────┘
                   │
    ┌──────────────┼──────────────┐
    │              │              │
┌───▼────┐  ┌─────▼─────┐  ┌───▼──────┐
│Postgres│  │  Neo4j    │  │ uploads/ │
│+pgvector│  │ (知识图谱) │  │ (原始文件)│
└────────┘  └───────────┘  └──────────┘
```

## 技术栈

| 层 | 选型 | 说明 |
|---|---|---|
| 后端 | Python 3.12+ / FastAPI | uv 管理环境 |
| 数据库 | Postgres 16 + pgvector | `pgvector/pgvector:pg16` Docker 镜像 |
| 图数据库 | Neo4j 5 | 复用现有 Docker 容器 (`neo4j-graphiti`) |
| Embedding | nomic-embed-text (768d) via Ollama | 本地运行，零成本，`config/model_config.yaml` 可切换 |
| 守门模型 | 开源小模型（待确认） | 用于 overview 二次打分，`config/model_config.yaml` 可切换 |
| LLM | 待配置 | 用于 overview 生成 + 实体提取 + Agent 答案合成，`config/model_config.yaml` 可切换 |
| 前端 | React + Vite | md 编辑器 `@uiw/react-md-editor` |
| MCP | Python MCP SDK (`mcp`) | streamable HTTP 传输 |
| 文件解析 | pypdf / python-docx / python-pptx / pytesseract | 策略模式 |

**模型配置轻量化**：所有模型（embedding / gatekeeper / llm）统一定义在 `config/model_config.yaml` 中，支持运行时热加载切换，无需改代码。

## 数据模型

### Postgres Schema

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- 文件表
CREATE TABLE documents (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title        TEXT NOT NULL,
    file_type    TEXT NOT NULL,  -- 'markdown'|'pdf'|'docx'|'pptx'|'image'|...
    raw_text     TEXT NOT NULL DEFAULT '',
    overview     TEXT NOT NULL DEFAULT '',  -- LLM 生成的索引摘要
    file_path    TEXT,           -- 原始文件本地路径
    content_hash TEXT,           -- SHA256，用于检测内容变化
    status       TEXT NOT NULL DEFAULT 'pending',
                 -- pending → processing → indexed / failed
    error_msg    TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_documents_status ON documents(status);
CREATE INDEX idx_documents_type ON documents(file_type);
CREATE INDEX idx_documents_title_trgm ON documents USING gin(title gin_trgm_ops);

-- 分块表
CREATE TABLE chunks (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    doc_id       UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index  INTEGER NOT NULL,
    chunk_text   TEXT NOT NULL,
    embedding    vector(768),           -- nomic-embed-text 维度，可通过配置切换
    overview     TEXT NOT NULL DEFAULT '',  -- 冗余自 documents.overview
    doc_uri      TEXT NOT NULL,             -- doc_id:标题
    token_count  INTEGER,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(doc_id, chunk_index)
);

CREATE INDEX idx_chunks_embedding ON chunks
    USING hnsw (embedding vector_cosine_ops);
-- 注：若切换 embedding 模型导致维度变化，
-- 需 DROP + CREATE 索引并 re-embed 所有 chunks
```

### Neo4j 图谱模型

**节点类型**：
- `Document` — 文件节点，`doc_id` 关联 Postgres
- 核心实体：`Person`、`Company`、`Building`、`Facility`、`Space`、`WorkOrder`、`Complaint`、`Issue`、`Contract`、`Regulation`、`Event`
- LLM 开放类型：任意自定义 label

**关系类型**：
- 文件级：`REFERENCES`、`SAME_TOPIC`、`ANALYZES`
- 实体级核心：`WORKS_AT`、`MANAGES`、`LOCATED_IN`、`PARKING_AT`、`BELONGS_TO`、`RESPONSIBLE_FOR`、`REPORTED`、`FILED_BY`、`ASSIGNED_TO`、`RESOLVES`、`COMPLAINS_ABOUT`、`PROVIDES_SERVICE`、`GOVERNS`、`RELATED_EVENT`
- LLM 开放类型：任意自定义关系

实体/关系类型定义存储在 `config/entity_schema.yaml`，支持运行时热加载，修改配置即可增删类型，无需改代码。

### entity_schema.yaml 示例

```yaml
entity_types:
  core:
    - Person
    - Company
    - Building
    - Facility
    - Space
    - WorkOrder
    - Complaint
    - Issue
    - Contract
    - Regulation
    - Event
  open: true  # 允许 LLM 补充自定义类型

relation_types:
  core:
    - WORKS_AT
    - MANAGES
    - LOCATED_IN
    - PARKING_AT
    - BELONGS_TO
    - RESPONSIBLE_FOR
    - REPORTED
    - FILED_BY
    - ASSIGNED_TO
    - RESOLVES
    - COMPLAINS_ABOUT
    - PROVIDES_SERVICE
    - GOVERNS
    - RELATED_EVENT
    - REFERENCES
    - SAME_TOPIC
    - ANALYZES
  open: true
```

## 文件入库 Pipeline

```
用户上传文件 → POST /documents/upload
    │
    ▼  (asyncio.Task 异步执行)
1. 存储原始文件 → uploads/{doc_id}/{filename}
    │
    ▼
2. 文本提取 (Extractor 策略模式)
   - MarkdownExtractor: md, txt
   - PDFExtractor: pypdf
   - DocxExtractor: python-docx
   - PPTXExtractor: python-pptx
   - ImageExtractor: pytesseract OCR
    │
    ▼
3. LLM 分析 (单次调用，多输出)
   输入: raw_text
   输出:
   - overview: 文档摘要（用于检索守门）
   - entities: [{name, type, description}]
   - relations: [{from, to, type, description}]
   - file_relations: [{related_doc_title, type, reason}]
    │
    ▼
4. Chunking
   - 语义段落切分，~500 tokens/chunk
   - 保留顺序 + 上下文窗口重叠(~50 tokens)
    │
    ▼
5. Embedding
   - nomic-embed-text via Ollama (768d)
   - 批量 embed 所有 chunks
   - 模型可配置，切换后自动 re-embed
    │
    ▼
6. 写入存储
   - Postgres: documents + chunks 表
   - Neo4j: 实体节点 + 关系边 + Document 节点
    │
    ▼
status = indexed
```

**幂等性**：通过 `content_hash` (SHA256) 判断，内容未变则跳过整个 pipeline。

**编辑触发 re-index**：`PUT /documents/{id}/content` 保存后，重新执行步骤 3-6（跳过文本提取，因为 md 内容直接可用）。

## 检索流程（三层漏斗）

### 第一层：向量粗筛

```
query → embed (nomic-embed-text via Ollama) → pgvector 余弦相似度
→ Top-K chunks (默认 K=20)
→ 返回: chunk_text, score, overview, doc_uri
```

HNSW 索引保证查询性能（`idx_chunks_embedding`）。

### 第二层：Overview 守门（小模型打分）

```
对每个命中的 chunk:
  overview_embedding = embed(overview, model=gatekeeper_model)
  similarity = cosine_sim(query_embedding, overview_embedding)
  if similarity >= threshold (0.7):
    keep
  else:
    filter out
```

- 守门模型在 `config/model_config.yaml` 中配置，支持本地 Ollama 或 API 模型
- 阈值可配置，默认 0.7
- overview embedding 可缓存（同文档的 overview 相同）

### 第三层：图谱增强 + Agent 精读

```
1. 从存活 chunks 的 doc_uri → 获取原文全文
2. Neo4j 查询相关实体（1-2 跳）：
   MATCH (d:Document {doc_id: $id})-[*1..2]-(related)
   RETURN related, relationships
3. Agent (GPT-4o) 综合：
   - 原文片段
   - 图谱上下文（实体+关系）
   - 用户 query
   → 生成答案 + 引用来源列表 + 相关实体关系
```

**输出格式**：
```json
{
  "answer": "合成的答案文本",
  "sources": [
    {"doc_id": "...", "title": "...", "chunk_text": "...", "score": 0.85}
  ],
  "related_entities": [
    {"name": "A栋", "type": "Building", "relations": [...]}
  ]
}
```

## API 设计

### REST API

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/documents/upload` | 上传文件 → 返回 doc_id + status |
| PUT | `/documents/{id}/content` | 在线编辑 md → 触发 re-index |
| GET | `/documents/{id}` | 文件详情（含 status、overview、chunks） |
| GET | `/documents` | 文件列表（分页，按 type/status 筛选） |
| DELETE | `/documents/{id}` | 删除文件（级联删 chunks + Neo4j） |
| POST | `/search` | 语义检索（三层漏斗） |
| GET | `/graph/entity/{name}` | 查询实体详情 + 关联关系 |
| GET | `/graph/neighbors/{id}` | 获取实体 N 跳邻居 |

### MCP Tools（同进程，streamable HTTP）

| Tool | 对应 | 说明 |
|---|---|---|
| `search` | POST /search | 语义检索 |
| `get_document` | GET /documents/{id} | 获取文件详情 |
| `query_graph` | GET /graph/* | 图谱查询 |
| `upload_document` | POST /documents/upload | 上传文件 |

MCP Server 和 FastAPI 共享 `KnowledgeBase` 业务逻辑层，零代码重复。

## 前端设计

**技术**：React + Vite + `@uiw/react-md-editor`

### 页面结构

```
┌─────────────────────────────────────────────┐
│  顶部：搜索栏 + 上传按钮                      │
├────────────────┬────────────────────────────┤
│  左侧：文件树   │  右侧：主内容区              │
│  - 按类型分组   │  搜索结果页 / 文件详情页     │
│  - 状态标识     │  (md 编辑器 / 文件预览)     │
└────────────────┴────────────────────────────┘
```

### 核心页面

1. **搜索页**：输入框 → 答案 + 引用文件卡片 + 实体关系可视化
2. **文件列表**：左侧树形目录 + 类型/状态筛选
3. **文件编辑**：md 在线编辑，其他类型展示 overview + 原文预览
4. **状态指示**：pending / processing / indexed / failed 标签

## 项目结构

```
team-knowledge-base/
├── config/
│   ├── entity_schema.yaml      # 实体/关系类型配置（可热加载）
│   └── model_config.yaml       # 模型配置（embedding/gatekeeper/llm）
├── src/
│   ├── api/
│   │   ├── routes.py           # FastAPI 路由
│   │   └── mcp_server.py       # MCP Server
│   ├── core/
│   │   ├── knowledge_base.py   # 核心业务逻辑（共享层）
│   │   ├── search.py           # 三层漏斗检索
│   │   └── graph.py            # Neo4j 图谱操作
│   ├── pipeline/
│   │   ├── extractors/         # 文件提取策略
│   │   │   ├── base.py
│   │   │   ├── markdown.py
│   │   │   ├── pdf.py
│   │   │   ├── docx.py
│   │   │   ├── pptx.py
│   │   │   └── image.py
│   │   ├── chunker.py          # 文本分块
│   │   ├── embedder.py         # 向量嵌入
│   │   ├── analyzer.py         # LLM 分析（overview + 实体提取）
│   │   └── pipeline.py         # Pipeline 编排
│   ├── db/
│   │   ├── models.py           # SQLAlchemy/SQLModel 模型
│   │   ├── postgres.py         # Postgres 连接 + 操作
│   │   └── neo4j_client.py     # Neo4j 连接 + 操作
│   └── main.py                 # FastAPI app 入口
├── frontend/
│   ├── src/
│   │   ├── pages/              # 搜索页、文件列表、文件编辑
│   │   ├── components/         # UI 组件
│   │   └── App.tsx
│   ├── package.json
│   └── vite.config.ts
├── uploads/                    # 原始文件存储
├── docker-compose.yml          # Postgres(pgvector) + 应用
├── pyproject.toml              # Python 依赖 (uv)
└── .env                        # API keys + DB URLs
```

## Docker Compose

```yaml
services:
  postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_DB: knowledge_base
      POSTGRES_USER: kb_user
      POSTGRES_PASSWORD: kb_pass
    ports:
      - "5433:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data

  # Neo4j 复用现有容器 (neo4j-graphiti, port 7474/7687)

volumes:
  pgdata:
```

注：Postgres 端口映射到 5433，避免与现有 crewstudio-postgres (5432) 冲突。

## 模型配置 (model_config.yaml)

```yaml
embedding:
  provider: ollama           # ollama | openai | custom
  model: nomic-embed-text    # 当前模型
  dimensions: 768
  base_url: http://localhost:11434

gatekeeper:
  provider: ollama           # ollama | openai | custom
  model: todo                # 待确认开源小模型
  dimensions: 768            # 与 embedding 同维度（复用 embed 函数）
  threshold: 0.7             # overview 守门阈值

llm:
  provider: todo             # ollama | openai | custom
  model: todo                # 待配置
  base_url: todo
  api_key: todo              # 如需要
```

切换模型时只需修改此文件，系统热加载生效。切换 embedding 模型需 re-embed 所有 chunks。

## 权限模型

MVP 阶段：全员平等，不做权限控制。后续可通过增加 `role` 字段和中间件扩展。

## 环境依赖

- Python 3.12+（uv 管理）
- Docker（Postgres + pgvector）
- Neo4j 5（现有容器）
- Ollama（本地 embedding 模型 `nomic-embed-text`）
- LLM API Key（待配置）
