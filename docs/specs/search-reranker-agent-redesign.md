# 检索架构重构：Reranker 守门 + Agent 对接

## 概述

将三层漏斗检索中的守门层从 cosine similarity 改为本地部署的 bge-reranker-v2-m3 模型，同时移除答案合成，搜索结果直接对接 Agent（如 Qoder）由其整合回答用户。

## 背景

### 当前架构问题

1. **守门层精度不足**：当前使用 overview embedding 与 query embedding 的 cosine similarity 做守门，这是双编码器（bi-encoder）方式，无法捕捉 query-chunk 之间的深层语义交互
2. **答案合成职责错位**：当前 search API 试图合成答案，但实际场景是对接 Agent（如 Qoder），应由 Agent 负责答案生成
3. **守门模型未实际部署**：`model_config.yaml` 中 gatekeeper.model 为 `todo`，未真正运行

### 目标

1. 用 bge-reranker-v2-m3（交叉编码器）替换 cosine similarity 守门，提升检索精度
2. 搜索结果返回原始 chunks + 实体关系，由 Agent 合成答案
3. Web 前端简化，专注文件浏览/编辑，移除搜索展示

## 架构变更

### 检索流程

```
改造前：
向量粗筛(Top-20) → cosine gatekeeper(overview) → 图谱增强 → SearchResult(answer + sources + entities)

改造后：
向量粗筛(Top-20) → Reranker(overview 打分) → 图谱增强 → SearchResult(chunks + entities)
```

### 数据流

```
Agent (Qoder)
    │
    │ MCP tool: search(query)
    ▼
┌──────────────────────────────────────────────────┐
│  search.py                                       │
│                                                  │
│  1. 向量粗筛                                      │
│     query → embed → pgvector cosine → Top-20     │
│                                                  │
│  2. Reranker 守门                                 │
│     for each candidate:                          │
│       score = CrossEncoder(query, overview)      │
│     sort by score, keep top_n (threshold ≥ 0.3)  │
│                                                  │
│  3. 图谱增强                                      │
│     survivors → Neo4j 1-2 跳查询 → entities      │
└────────────────────┬─────────────────────────────┘
                     │
                     ▼
              {chunks: [...], related_entities: [...]}
                     │
                     ▼
            Agent 整合 query + chunks + entities
                     │
                     ▼
            Agent 生成最终回答 → 用户
```

## Reranker 服务

### 模型选型

| 属性 | 值 |
|---|---|
| 模型 | BAAI/bge-reranker-v2-m3 |
| 参数量 | ~567M |
| 类型 | Cross-encoder（交叉编码器） |
| 多语言 | 支持（中/英/日/韩等 100+ 语言） |
| 推理方式 | CPU / MPS (Apple Silicon) |
| 权重大小 | ~1.1GB |

### 集成方式

进程内嵌：在 `search.py` 中通过 `sentence-transformers` 直接加载模型。

```python
# src/core/reranker.py
from sentence_transformers import CrossEncoder

class Reranker:
    def __init__(self, model_name="BAAI/bge-reranker-v2-m3"):
        self.model = CrossEncoder(model_name)

    async def rerank(self, query: str, texts: list[str]) -> list[float]:
        pairs = [(query, text) for text in texts]
        scores = self.model.predict(pairs)
        return scores.tolist()

reranker = Reranker()
```

**生命周期**：
- 首次启动：从 HuggingFace 下载模型权重（~1.1GB），缓存到 `~/.cache/huggingface/`
- 后续启动：从本地缓存加载，约 3-5s
- 推理：sentence-transformers 自动检测 MPS（Apple Silicon GPU 加速）

### Reranker 打分逻辑

```python
# 对每个候选 chunk，用 reranker 对 (query, overview) 打分
overviews = [c["overview"] for c in candidates]
scores = await reranker.rerank(query, overviews)

# 按分数排序，阈值过滤 + Top-N
scored = sorted(zip(candidates, scores), key=lambda x: -x[1])
survivors = [c for c, s in scored if s >= threshold][:top_n]
```

## SearchResult 数据结构

### 改造前

```python
@dataclass
class SearchResult:
    answer: str                                    # LLM 合成答案
    sources: list[SearchSource]                    # chunks
    related_entities: list[dict]                   # 图谱实体
```

### 改造后

```python
@dataclass
class SearchChunk:
    doc_id: str
    title: str
    chunk_text: str
    reranker_score: float                          # reranker 相关性分数
    vector_score: float                            # 向量余弦相似度

@dataclass
class SearchResult:
    chunks: list[SearchChunk]                      # reranker 过滤后的 chunks
    related_entities: list[dict]                   # 图谱实体（保留）
```

**移除 `answer` 字段**：答案合成由 Agent 负责，search 只返回原始数据。

## MCP Tool 变更

### search tool 返回

```json
{
  "chunks": [
    {
      "doc_id": "uuid",
      "title": "物业管理制度.md",
      "chunk_text": "停车场管理相关...",
      "reranker_score": 0.85,
      "vector_score": 0.72
    }
  ],
  "related_entities": [
    {
      "name": "A栋",
      "type": "Building",
      "relations": [{"target": "技术研发部", "type": "LOCATED_IN"}]
    }
  ]
}
```

Agent 读取后自行整合用户 query 与知识，生成回答。

## REST API 变更

`POST /search` 保留端点，返回同 MCP 结构（无 answer）。

## Web 前端变更

- 移除搜索相关组件（搜索栏、搜索结果展示页）
- `DocumentListPage.tsx`：移除搜索功能，专注文件列表 + 筛选
- `DocumentDetailPage.tsx`：保留文件详情 + 编辑
- 删除搜索结果展示相关代码

## 配置变更

### model_config.yaml

```yaml
gatekeeper:
  provider: sentence-transformers
  model: BAAI/bge-reranker-v2-m3
  threshold: 0.3                # reranker 分数阈值
  top_n: 10                     # reranker 后保留 Top-N
```

### 依赖变更

- 新增：`sentence-transformers`（含 torch、transformers、tokenizers 等）
- 无需额外 Docker 容器
- `.env` 无变化

## 影响范围

| 文件 | 变更类型 | 说明 |
|---|---|---|
| `src/core/reranker.py` | 新增 | Reranker 服务封装 |
| `src/core/search.py` | 重构 | 替换 gatekeeper → reranker，调整 SearchResult |
| `src/core/knowledge_base.py` | 修改 | search() 返回类型适配 |
| `src/api/mcp_server.py` | 修改 | search tool 返回结构适配 |
| `src/api/routes.py` | 修改 | /search 响应适配 |
| `config/model_config.yaml` | 修改 | gatekeeper 配置更新 |
| `pyproject.toml` | 修改 | 新增 sentence-transformers 依赖 |
| `frontend/src/pages/DocumentListPage.tsx` | 修改 | 移除搜索功能 |
| `frontend/src/api/client.ts` | 修改 | 搜索响应类型调整 |
