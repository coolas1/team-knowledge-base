# 三层知识图谱设计：Chunk 级抽取 → 文档内聚合 → 跨文档关联

## 背景与动机

当前知识图谱以 Document 为粒度做实体抽取，存在以下问题：

1. **粒度过粗**：一篇文档只做一次 LLM 分析，所有 chunk 的实体混在一起，无法追溯具体 chunk 来源
2. **无 Chunk 级关联**：chunk 间的语义关联（如 chunk1 的实体 A 和 chunk3 的实体 B 通过中间 chunk 有关系链）无法体现
3. **file_relations 被丢弃**：LLM 提取的跨文档关联未写入 Neo4j
4. **Document 节点占据图谱拓扑**：图谱中混入了 Document 节点，不够纯粹

## 设计目标

构建三层知识图谱：

| 层 | 名称 | 职责 |
|---|---|---|
| L1 | Chunk 内抽取 | 每个 chunk 独立 LLM 实体+关系抽取 |
| L2 | 文档内聚合 | 同文档多 chunk 实体对齐（MERGE by name） |
| L3 | 跨文档关联 | 跨文档实体自然合并 + file_relations 显式关联 |

**核心原则**：Neo4j 图谱中只有 Entity 节点（纯概念/实体），不含 Document/Chunk 节点。文件和 chunk 信息作为溯源属性存储在 Entity 节点和关系边上。

## Neo4j 图谱结构

### Entity 节点

```
(:Entity {
  name: "张伟",
  entity_type: "Person",
  description: "物业经理，负责整体管理",
  sources: [
    {doc_id: "abc-123", chunk_index: 0, doc_title: "物业管理制度.md"},
    {doc_id: "def-456", chunk_index: 2, doc_title: "组织架构.md"}
  ]
})
```

- **MERGE by name**：同一实体全局唯一
- **sources**：列表属性，记录该实体在哪些 doc/chunk 中被提及
- 每次发现新来源时，追加到 sources 列表（去重）

### Entity↔Entity 关系边

```
(:Entity {name:"张伟"}) -[:MANAGES {
  description: "负责阳光科技园区的整体管理",
  sources: [{doc_id: "abc-123", chunk_index: 0}]
}]-> (:Entity {name:"阳光科技园区"})
```

- 关系边也带 sources 溯源
- MERGE 去重：同类型同方向的关系合并

### Document 节点（仅元数据，不参与图谱拓扑）

```
(:Document {
  doc_id: "abc-123",
  title: "物业管理制度.md",
  file_type: "markdown",
  overview: "文档详细描述了园区的物业管理制度..."
})
```

- 保留 Document 节点仅用于存储文档级元数据（title, overview）
- 不再创建 Document↔Entity 的 REFERENCES 边
- 实体溯源通过 Entity.sources 属性查询

## L1: Chunk 级抽取

### Analyzer 改造

新增 `analyze_chunk()` 方法：

```python
async def analyze_chunk(self, chunk_text: str, doc_title: str, chunk_index: int) -> ChunkAnalysisResult:
    """对单个 chunk 做实体和关系抽取。"""
```

**输入**：单个 chunk 文本 + 文档标题 + chunk 序号
**输出**：

```python
@dataclass
class ChunkAnalysisResult:
    chunk_index: int
    entities: list[Entity]       # 该 chunk 中提到的实体
    relations: list[Relation]    # 该 chunk 中实体间的关系
```

**Overview 保留**：文档级 overview 通过单独的 `analyze_overview()` 方法生成（复用现有文档级分析，只提取 overview 字段）。

### LLM Prompt 调整

Chunk 级 prompt 相比文档级 prompt 的变化：
- 移除 overview 要求（overview 由文档级调用生成）
- 移除 file_relations 要求（chunk 级无法判断跨文档关联）
- 聚焦当前 chunk 文本内的实体和关系提取

### Pipeline 流程

```
旧流程：提取文本 → 文档级 LLM 分析 → 分块 → Embedding → 写入
新流程：提取文本 → 分块 → 文档级 overview 生成 + 逐 Chunk LLM 分析 → Embedding → 写入
```

## L2: 文档内聚合

### 实体 MERGE 策略

Neo4j Cypher：

```cypher
MERGE (e:Entity {name: $name})
SET e.entity_type = $entity_type,
    e.description = CASE WHEN size($description) > size(coalesce(e.description, ''))
                         THEN $description ELSE e.description END
WITH e
UNWIND $new_sources AS src
WITH e, src
WHERE NOT any(s IN coalesce(e.sources, []) WHERE s.doc_id = src.doc_id AND s.chunk_index = src.chunk_index)
SET e.sources = coalesce(e.sources, []) + [src]
```

**关键点**：
- MERGE by name → 同文档不同 chunk 提到的 "UModel" 自然合并
- description 保留较长的版本（信息量更大）
- sources 追加时去重（doc_id + chunk_index 组合唯一）

### 关系 MERGE 策略

```cypher
MATCH (a:Entity {name: $from_name})
MATCH (b:Entity {name: $to_name})
MERGE (a)-[r:RELATION_TYPE]->(b)
SET r.description = CASE WHEN size($description) > size(coalesce(r.description, ''))
                         THEN $description ELSE r.description END
// sources 追加去重
```

## L3: 跨文档关联

### 实体自然合并

MERGE by name 天然跨文档。当文档 B 提到 "张伟"（已在文档 A 中建立），Neo4j 自动合并为同一节点，sources 追加文档 B 的来源。

### file_relations 显式关联

LLM 在文档级分析中提取的 `file_relations`（related_doc_title, type, reason）→ 写入 Neo4j：

1. Pipeline 中通过 Postgres 按 `related_doc_title` 查找目标 doc_id
2. 找到 → 创建 Document↔Document 边：
   ```cypher
   MATCH (d1:Document {doc_id: $source_doc_id})
   MATCH (d2:Document {doc_id: $target_doc_id})
   MERGE (d1)-[r:RELATED_TO {type: $relation_type}]->(d2)
   SET r.reason = $reason
   ```
3. 找不到 → 跳过（不创建占位节点）

## 搜索层增强

### graph_enrich 改造

当前逻辑：从存活 chunks 的 doc_id 出发 → 查 Document→Entity 关系 → 返回实体

新逻辑：
1. 从存活 chunks 的 (doc_id, chunk_index) 出发
2. 查询 Neo4j：找出 sources 包含这些 (doc_id, chunk_index) 的 Entity
3. 遍历 Entity 关系（1-2 跳）
4. 通过关联 Entity 的 sources 发现额外相关 chunk/doc
5. 返回 related_entities + related_docs

### SearchResult 扩展

```python
@dataclass
class SearchResult:
    chunks: list[SearchChunk]
    related_entities: list[dict]
    related_docs: list[dict]  # 新增：通过图谱发现的关联文档
```

`related_docs` 结构：
```json
{
  "doc_id": "xxx",
  "title": "组织架构.md",
  "relation_type": "SAME_TOPIC",
  "reason": "共同实体：张伟、李明"
}
```

## 涉及文件变更

| 文件 | 变更类型 | 说明 |
|---|---|---|
| `src/pipeline/analyzer.py` | 重构 | 新增 `analyze_chunk()` + `analyze_overview()`，保留 `analyze()` 兼容 |
| `src/pipeline/pipeline.py` | 重构 | `_write_graph` 改为逐 chunk 写入，处理 file_relations |
| `src/db/neo4j_client.py` | 重构 | `upsert_entity` 加 sources 溯源；移除 Document→Entity REFERENCES；新增 file_relation 方法 |
| `src/core/search.py` | 改造 | `graph_enrich` 从 sources 属性查询实体；新增 related_docs |
| `src/core/knowledge_base.py` | 微调 | 适配 search 返回结构变化 |
| `src/api/routes.py` | 微调 | /search 响应增加 related_docs |
| `src/api/mcp_server.py` | 微调 | search tool 返回增加 related_docs |

## 不做的事

- 不创建 Chunk 节点（chunk 信息存在 Entity.sources 中）
- 不创建 Document→Entity 的 REFERENCES 边（溯源通过 sources 属性）
- 不为不存在的文档创建占位节点
- 不改变 Postgres 层（Chunk 表结构不变）
- 不改变 Embedding/Reranker 逻辑
