# 三层知识图谱重构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将知识图谱从 Document 级改造为 Chunk 级三层结构：逐 chunk 抽取 → 文档内实体聚合 → 跨文档关联，Neo4j 中只保留 Entity 节点 + 溯源属性。

**Architecture:** 每个 chunk 独立调用 LLM 提取实体和关系（L1），通过 MERGE by name 在文档内自然合并同名实体（L2），跨文档也自然合并 + file_relations 显式关联（L3）。Entity 节点的 `sources` 属性记录来自哪些 doc+chunk，搜索时通过 sources 反查关联实体和文档。

**Tech Stack:** Python 3.12, FastAPI, Neo4j async driver, httpx, sentence-transformers, DashScope (qwen-turbo)

## Global Constraints

- Neo4j 中只有 Entity 节点（纯概念），无 Document/Chunk 节点参与图谱拓扑
- Document 节点仅保留为元数据容器（title, overview, file_type），不创建 Document↔Entity 的 REFERENCES 边
- Entity 溯源通过 `sources` 属性（JSON 字符串列表）存储在节点上
- 关系边也带 `sources` 属性
- file_relations 目标文档不存在时跳过，不创建占位节点
- 不改变 Postgres 层（Chunk 表结构不变）
- 不改变 Embedding/Reranker 逻辑

---

### Task 1: Neo4j Client — Entity 溯源模型

**Files:**
- Modify: `src/db/neo4j_client.py`

**Interfaces:**
- Consumes: nothing new
- Produces: `upsert_entity(doc_id, entity, source)` with sources tracking, `upsert_relation(from_name, to_name, relation_type, description, source)` with source on edge, `create_doc_relation(source_doc_id, target_doc_id, relation_type, reason)`, `find_entities_by_source(doc_id, chunk_index)`, `get_related_docs(doc_ids)`, removed `Document→Entity REFERENCES` edges

- [ ] **Step 1: 更新 EntityData 数据类**

```python
# src/db/neo4j_client.py - 更新 EntityData
@dataclass
class EntityData:
    name: str
    entity_type: str
    description: str = ""


@dataclass
class EntitySource:
    """实体溯源信息。"""
    doc_id: str
    chunk_index: int
    doc_title: str


@dataclass
class RelationData:
    from_name: str
    to_name: str
    relation_type: str
    description: str = ""
```

- [ ] **Step 2: 重写 upsert_entity — 加 sources 溯源，移除 Document→Entity**

替换现有的 `upsert_entity` 方法（当前 line 84-98）：

```python
async def upsert_entity(
    self, entity: EntityData, source: EntitySource
) -> None:
    """创建/更新实体节点，追加溯源来源。

    MERGE by name → 同名实体全局唯一。
    sources 存储为 JSON 字符串列表（Neo4j 不支持 list<map>）。
    去重逻辑在 Python 层处理。
    """
    import json

    new_source = {
        "doc_id": source.doc_id,
        "chunk_index": source.chunk_index,
        "doc_title": source.doc_title,
    }

    async with self._driver.session() as session:
        # 1. MERGE 实体节点，保留较长的 description
        await session.run(
            f"""
            MERGE (e:{entity.entity_type} {{name: $name}})
            SET e.entity_type = $entity_type,
                e.description = CASE
                    WHEN size($desc) > size(coalesce(e.description, ''))
                    THEN $desc
                    ELSE e.description
                END
            """,
            name=entity.name,
            entity_type=entity.entity_type,
            desc=entity.description,
        )

        # 2. 读取现有 sources，追加去重
        result = await session.run(
            """
            MATCH (e {name: $name})
            RETURN e.sources AS sources
            """,
            name=entity.name,
        )
        record = await result.single()
        sources_json = record["sources"] if record and record["sources"] else "[]"
        sources: list[dict] = json.loads(sources_json)

        # 去重检查
        is_dup = any(
            s["doc_id"] == new_source["doc_id"]
            and s["chunk_index"] == new_source["chunk_index"]
            for s in sources
        )
        if not is_dup:
            sources.append(new_source)
            await session.run(
                """
                MATCH (e {name: $name})
                SET e.sources = $sources
                """,
                name=entity.name,
                sources=json.dumps(sources, ensure_ascii=False),
            )
```

- [ ] **Step 3: 重写 create_relation → upsert_relation（带 source）**

替换现有的 `create_relation` 方法（当前 line 102-115）：

```python
async def upsert_relation(
    self, relation: RelationData, source: EntitySource
) -> None:
    """创建/更新实体间关系，带溯源。"""
    import json

    new_source = {
        "doc_id": source.doc_id,
        "chunk_index": source.chunk_index,
    }

    async with self._driver.session() as session:
        # 1. MERGE 关系，保留较长的 description
        await session.run(
            f"""
            MATCH (a {{name: $from_name}})
            MATCH (b {{name: $to_name}})
            MERGE (a)-[r:{relation.relation_type}]->(b)
            SET r.description = CASE
                    WHEN size($desc) > size(coalesce(r.description, ''))
                    THEN $desc
                    ELSE r.description
                END
            """,
            from_name=relation.from_name,
            to_name=relation.to_name,
            desc=relation.description,
        )

        # 2. 追加 sources 去重
        result = await session.run(
            f"""
            MATCH (a {{name: $from_name}})-[r:{relation.relation_type}]->(b {{name: $to_name}})
            RETURN r.sources AS sources
            """,
            from_name=relation.from_name,
            to_name=relation.to_name,
        )
        record = await result.single()
        if record:
            sources_json = record["sources"] if record["sources"] else "[]"
            sources: list[dict] = json.loads(sources_json)
            is_dup = any(
                s["doc_id"] == new_source["doc_id"]
                and s["chunk_index"] == new_source["chunk_index"]
                for s in sources
            )
            if not is_dup:
                sources.append(new_source)
                await session.run(
                    f"""
                    MATCH (a {{name: $from_name}})-[r:{relation.relation_type}]->(b {{name: $to_name}})
                    SET r.sources = $sources
                    """,
                    from_name=relation.from_name,
                    to_name=relation.to_name,
                    sources=json.dumps(sources, ensure_ascii=False),
                )
```

- [ ] **Step 4: 新增 create_doc_relation 方法**

在 `upsert_relation` 后新增：

```python
async def create_doc_relation(
    self,
    source_doc_id: str,
    target_doc_id: str,
    relation_type: str,
    reason: str,
) -> None:
    """创建 Document↔Document 显式关联（file_relations）。"""
    async with self._driver.session() as session:
        await session.run(
            """
            MATCH (d1:Document {doc_id: $source_doc_id})
            MATCH (d2:Document {doc_id: $target_doc_id})
            MERGE (d1)-[r:RELATED_TO {relation_type: $relation_type}]->(d2)
            SET r.reason = $reason
            """,
            source_doc_id=source_doc_id,
            target_doc_id=target_doc_id,
            relation_type=relation_type,
            reason=reason,
        )
```

- [ ] **Step 5: 新增 find_entities_by_source 查询方法**

在查询区域新增：

```python
async def find_entities_by_source(
    self, doc_id: str, chunk_index: int
) -> list[GraphQueryResult]:
    """查找 sources 包含指定 (doc_id, chunk_index) 的实体。"""
    import json

    async with self._driver.session() as session:
        result = await session.run(
            """
            MATCH (e)
            WHERE e.sources IS NOT NULL
              AND e.sources CONTAINS $doc_id_fragment
            RETURN e, labels(e) AS labels
            """,
            doc_id_fragment=doc_id,
        )
        records = await result.data()
        results = []
        for record in records:
            node = record["e"]
            labels = record["labels"]
            sources: list[dict] = json.loads(node.get("sources", "[]"))
            # 精确匹配 chunk_index
            if any(
                s["doc_id"] == doc_id and s["chunk_index"] == chunk_index
                for s in sources
            ):
                entity_type = next(
                    (l for l in labels if l not in ("Document",)), "Entity"
                )
                results.append(
                    GraphQueryResult(
                        name=node.get("name", ""),
                        entity_type=entity_type,
                        properties=dict(node),
                    )
                )
        return results
```

- [ ] **Step 6: 新增 get_related_docs 查询方法**

```python
async def get_related_docs(self, doc_ids: list[str]) -> list[dict]:
    """从指定文档出发，通过 Document↔Document 边查找关联文档。"""
    if not doc_ids:
        return []

    async with self._driver.session() as session:
        result = await session.run(
            """
            MATCH (d1:Document)-[r:RELATED_TO]-(d2:Document)
            WHERE d1.doc_id IN $doc_ids AND d2.doc_id NOT IN $doc_ids
            RETURN DISTINCT d2.doc_id AS doc_id,
                   d2.title AS title,
                   type(r) AS rel_type,
                   r.relation_type AS relation_type,
                   r.reason AS reason
            """,
            doc_ids=doc_ids,
        )
        records = await result.data()
        return [
            {
                "doc_id": r["doc_id"],
                "title": r["title"],
                "relation_type": r.get("relation_type", ""),
                "reason": r.get("reason", ""),
            }
            for r in records
        ]
```

- [ ] **Step 7: 更新 get_document_entities 查询（移除 REFERENCES 依赖）**

当前 `get_document_entities`（line 185-208）依赖 `Document→Entity REFERENCES` 边。改为通过 sources 查询：

```python
async def get_document_entities(self, doc_id: str) -> list[GraphQueryResult]:
    """获取某文档关联的所有实体（通过 sources 属性）。"""
    import json

    async with self._driver.session() as session:
        result = await session.run(
            """
            MATCH (e)
            WHERE e.sources IS NOT NULL
              AND e.sources CONTAINS $doc_id
            RETURN e, labels(e) AS labels
            """,
            doc_id=doc_id,
        )
        records = await result.data()
        results = []
        for record in records:
            node = record["e"]
            labels = record["labels"]
            entity_type = next(
                (l for l in labels if l not in ("Document",)), "Entity"
            )
            results.append(
                GraphQueryResult(
                    name=node.get("name", ""),
                    entity_type=entity_type,
                    properties=dict(node),
                )
            )
        return results
```

- [ ] **Step 8: 更新 delete_document_graph — 清理旧实体来源**

当前 `delete_document_graph`（line 69-80）删除 Document 及其关系。改造为：
1. 从所有 Entity 的 sources 中移除该 doc_id 的条目
2. 删除 sources 为空的孤立 Entity
3. 删除 Document 节点

```python
async def delete_document_graph(self, doc_id: str) -> None:
    """删除文档的图谱数据：清理实体 sources + 删 Document 节点。"""
    import json

    async with self._driver.session() as session:
        # 1. 从所有实体的 sources 中移除该 doc_id 的条目
        result = await session.run(
            """
            MATCH (e)
            WHERE e.sources IS NOT NULL
              AND e.sources CONTAINS $doc_id
            RETURN e.name AS name, e.sources AS sources
            """,
            doc_id=doc_id,
        )
        records = await result.data()
        for record in records:
            sources: list[dict] = json.loads(record["sources"])
            new_sources = [s for s in sources if s["doc_id"] != doc_id]
            await session.run(
                """
                MATCH (e {name: $name})
                SET e.sources = $sources
                """,
                name=record["name"],
                sources=json.dumps(new_sources, ensure_ascii=False) if new_sources else "[]",
            )

        # 2. 删除 sources 为空的孤立实体
        await session.run(
            """
            MATCH (e)
            WHERE e.sources = '[]' OR e.sources = ''
            DETACH DELETE e
            """
        )

        # 3. 删除 Document 节点及其 doc 级关系
        await session.run(
            """
            MATCH (d:Document {doc_id: $doc_id})
            OPTIONAL MATCH (d)-[r]-()
            DELETE r, d
            """,
            doc_id=doc_id,
        )
```

- [ ] **Step 9: 验证 Neo4j Client 改动**

Run: `cd /Users/caoyurui/study/team-knowledge-base && uv run python -c "from src.db.neo4j_client import Neo4jClient, EntityData, EntitySource, RelationData; print('imports OK')"`

Expected: `imports OK`

- [ ] **Step 10: Commit**

```bash
git add src/db/neo4j_client.py
git commit -m "refactor: Neo4j client — entity sources 溯源模型 + doc relations"
```

---

### Task 2: Analyzer Refactor — Chunk 级抽取

**Files:**
- Modify: `src/pipeline/analyzer.py`

**Interfaces:**
- Consumes: nothing new (uses existing httpx + config)
- Produces: `ChunkAnalysisResult` dataclass, `analyze_chunk(chunk_text, doc_title, chunk_index)` method, `analyze_overview(text, title)` method

- [ ] **Step 1: 新增 ChunkAnalysisResult 数据类**

在 `FileRelation` 后（约 line 37）新增：

```python
@dataclass
class ChunkAnalysisResult:
    """单个 chunk 的 LLM 分析结果。"""
    chunk_index: int
    entities: list[Entity] = field(default_factory=list)
    relations: list[Relation] = field(default_factory=list)
```

- [ ] **Step 2: 新增 _build_chunk_prompt 静态方法**

在 `Analyzer` 类中新增：

```python
@staticmethod
def _build_chunk_prompt(chunk_text: str, doc_title: str, schema: dict) -> str:
    """构建 chunk 级分析 prompt。"""
    entity_types = schema.get("entity_types", {})
    relation_types = schema.get("relation_types", {})
    core_entities = ", ".join(entity_types.get("core", []))
    core_relations = ", ".join(relation_types.get("core", []))
    open_entities = entity_types.get("open", True)
    open_relations = relation_types.get("open", True)

    entity_instruction = f"核心实体类型: [{core_entities}]"
    if open_entities:
        entity_instruction += "。你也可以根据内容补充自定义实体类型。"
    relation_instruction = f"核心关系类型: [{core_relations}]"
    if open_relations:
        relation_instruction += "。你也可以根据内容补充自定义关系类型。"

    return f"""你是一个专业的文档分析助手。请分析以下文档片段中的实体和关系。

**所属文档:** {doc_title}

**片段内容:**
{chunk_text[:4000]}

**要求:**
1. **entities**: 提取片段中的重要实体。
   {entity_instruction}
   每个实体包含: name(名称), type(类型), description(简要描述)

2. **relations**: 提取实体之间的关系。
   {relation_instruction}
   每个关系包含: from_name(起始实体), to_name(目标实体), type(关系类型), description(关系描述)

请严格返回 JSON 格式:
```json
{{
  "entities": [{{"name": "...", "type": "...", "description": "..."}}],
  "relations": [{{"from_name": "...", "to_name": "...", "type": "...", "description": "..."}}]
}}
```"""
```

- [ ] **Step 3: 新增 analyze_chunk 方法**

```python
async def analyze_chunk(
    self, chunk_text: str, doc_title: str, chunk_index: int
) -> ChunkAnalysisResult:
    """对单个 chunk 做实体和关系抽取。"""
    provider = self._config.get("provider", "todo")
    if provider == "todo":
        return ChunkAnalysisResult(chunk_index=chunk_index)

    prompt = self._build_chunk_prompt(chunk_text, doc_title, self._schema)

    if provider == "ollama":
        raw = await self._call_ollama(prompt)
    elif provider in ("openai", "custom"):
        raw = await self._call_openai_compatible(prompt)
    else:
        return ChunkAnalysisResult(chunk_index=chunk_index)

    return self._parse_chunk_response(raw, chunk_index)
```

- [ ] **Step 4: 新增 _parse_chunk_response 方法**

```python
@staticmethod
def _parse_chunk_response(raw: str, chunk_index: int) -> ChunkAnalysisResult:
    """解析 chunk 级 LLM 返回的 JSON。"""
    try:
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1])
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.warning(f"chunk {chunk_index} LLM 返回解析失败: {raw[:100]}")
        return ChunkAnalysisResult(chunk_index=chunk_index)

    entities = [
        Entity(
            name=e.get("name", ""),
            type=e.get("type", "Unknown"),
            description=e.get("description", ""),
        )
        for e in data.get("entities", [])
    ]
    relations = [
        Relation(
            from_name=r.get("from_name", ""),
            to_name=r.get("to_name", ""),
            type=r.get("type", "RELATED_TO"),
            description=r.get("description", ""),
        )
        for r in data.get("relations", [])
    ]
    return ChunkAnalysisResult(
        chunk_index=chunk_index, entities=entities, relations=relations
    )
```

- [ ] **Step 5: 新增 analyze_overview 方法**

复用现有 `analyze()` 但只提取 overview + file_relations：

```python
async def analyze_overview(
    self, text: str, title: str
) -> AnalysisResult:
    """文档级分析，仅提取 overview + file_relations。"""
    provider = self._config.get("provider", "todo")
    if provider == "todo":
        return AnalysisResult(
            overview=f"[待 LLM 生成] {title}",
            entities=[],
            relations=[],
            file_relations=[],
        )

    prompt = self._build_overview_prompt(title, text)

    if provider == "ollama":
        raw = await self._call_ollama(prompt)
    elif provider in ("openai", "custom"):
        raw = await self._call_openai_compatible(prompt)
    else:
        return AnalysisResult(overview=f"[未知 provider] {title}")

    return self._parse_overview_response(raw)
```

- [ ] **Step 6: 新增 _build_overview_prompt 和 _parse_overview_response**

```python
@staticmethod
def _build_overview_prompt(title: str, text: str) -> str:
    """构建文档级 overview + file_relations prompt。"""
    return f"""你是一个专业的文档分析助手。请为以下文档生成摘要和跨文档关联推测。

**文档标题:** {title}

**文档内容:**
{text[:8000]}

**要求:**
1. **overview**: 写一段 2-3 句话的文档摘要。
2. **file_relations**: 如果文档提到了与其他文档/文件相关的内容，推测可能的文件关联。
   每个关联包含: related_doc_title(相关文档标题), type(关联类型如 REFERENCES/SAME_TOPIC/ANALYZES), reason(关联原因)

请严格返回 JSON 格式:
```json
{{
  "overview": "...",
  "file_relations": [{{"related_doc_title": "...", "type": "...", "reason": "..."}}]
}}
```"""

@staticmethod
def _parse_overview_response(raw: str) -> AnalysisResult:
    """解析 overview + file_relations 响应。"""
    try:
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1])
        data = json.loads(text)
    except json.JSONDecodeError:
        return AnalysisResult(overview=f"[LLM 返回解析失败] {raw[:200]}")

    file_relations = [
        FileRelation(
            related_doc_title=f.get("related_doc_title", ""),
            type=f.get("type", "REFERENCES"),
            reason=f.get("reason", ""),
        )
        for f in data.get("file_relations", [])
    ]
    return AnalysisResult(
        overview=data.get("overview", ""),
        file_relations=file_relations,
    )
```

需要在文件顶部添加 `logger`：
```python
import logging
logger = logging.getLogger(__name__)
```

- [ ] **Step 7: 验证 Analyzer 改动**

Run: `cd /Users/caoyurui/study/team-knowledge-base && uv run python -c "from src.pipeline.analyzer import Analyzer, ChunkAnalysisResult; a = Analyzer(); print(f'methods: analyze_chunk={hasattr(a, \"analyze_chunk\")}, analyze_overview={hasattr(a, \"analyze_overview\")}'); print('OK')"`

Expected: `methods: analyze_chunk=True, analyze_overview=True` + `OK`

- [ ] **Step 8: Commit**

```bash
git add src/pipeline/analyzer.py
git commit -m "feat: analyzer — chunk 级抽取 + overview 分离"
```

---

### Task 3: Pipeline 适配 — 逐 Chunk 写入图谱

**Files:**
- Modify: `src/pipeline/pipeline.py`

**Interfaces:**
- Consumes: `ChunkAnalysisResult` from Task 2, `EntitySource` from Task 1
- Produces: per-chunk graph writing, file_relations resolution

- [ ] **Step 1: 更新 Pipeline imports**

```python
from src.db.neo4j_client import Neo4jClient, EntityData, EntitySource, RelationData
from src.pipeline.analyzer import analyzer, ChunkAnalysisResult
```

- [ ] **Step 2: 重写 process_file 中的 LLM 分析 + 图谱写入流程**

将 `process_file` 中的步骤 4-8（当前 line 64-125）替换为：

```python
                # 4. 文本分块（先分块再分析）
                chunks = chunk_text(raw_text)
                logger.info(f"文档 {doc_id} 分块完成: {len(chunks)} chunks")

                # 5. 文档级 overview + file_relations
                doc_analysis = await analyzer.analyze_overview(raw_text, title)
                logger.info(
                    f"文档 {doc_id} overview 生成完成, "
                    f"{len(doc_analysis.file_relations)} file_relations"
                )

                # 6. 逐 Chunk LLM 分析
                chunk_analyses: list[ChunkAnalysisResult] = []
                for chunk in chunks:
                    ca = await analyzer.analyze_chunk(
                        chunk.text, title, chunk.index
                    )
                    chunk_analyses.append(ca)
                    logger.info(
                        f"文档 {doc_id} chunk[{chunk.index}]: "
                        f"{len(ca.entities)} 实体, {len(ca.relations)} 关系"
                    )

                # 7. Embedding
                if chunks:
                    texts = [c.text for c in chunks]
                    embeddings = await embedder.embed_batch(texts)
                else:
                    embeddings = []

                # 8. 写入 Postgres（overview 使用 doc_analysis.overview）
                await session.execute(
                    Chunk.__table__.delete().where(Chunk.doc_id == doc_id)
                )

                doc_uri = f"{doc_id}:{title}"
                for chunk, embedding in zip(chunks, embeddings):
                    db_chunk = Chunk(
                        doc_id=doc_id,
                        chunk_index=chunk.index,
                        chunk_text=chunk.text,
                        embedding=embedding,
                        overview=doc_analysis.overview,
                        doc_uri=doc_uri,
                        token_count=chunk.token_count,
                    )
                    session.add(db_chunk)

                await session.execute(
                    update(Document)
                    .where(Document.id == doc_id)
                    .values(
                        raw_text=raw_text,
                        overview=doc_analysis.overview,
                        content_hash=content_hash,
                        status="indexed",
                        error_msg=None,
                    )
                )
                await session.commit()
                logger.info(f"文档 {doc_id} Postgres 写入完成")

                # 9. 写入 Neo4j（三层图谱）
                await self._write_graph(
                    doc_id=str(doc_id),
                    title=title,
                    file_type=file_type,
                    overview=doc_analysis.overview,
                    chunk_analyses=chunk_analyses,
                    file_relations=doc_analysis.file_relations,
                    session=session,
                )
                logger.info(f"文档 {doc_id} Pipeline 完成 ✓")
```

- [ ] **Step 3: 重写 _write_graph 方法**

```python
async def _write_graph(
    self,
    doc_id: str,
    title: str,
    file_type: str,
    overview: str,
    chunk_analyses: list[ChunkAnalysisResult],
    file_relations: list,
    session,
) -> None:
    """三层图谱写入：L1 chunk 级 + L2 文档内聚合 + L3 跨文档关联。"""
    # Document 节点（仅元数据）
    await self._neo4j.upsert_document_node(
        doc_id=doc_id, title=title, file_type=file_type, overview=overview
    )

    # L1+L2: 逐 chunk 写入实体和关系（MERGE 自然聚合）
    for ca in chunk_analyses:
        source = EntitySource(
            doc_id=doc_id, chunk_index=ca.chunk_index, doc_title=title
        )

        # 实体节点
        for entity in ca.entities:
            await self._neo4j.upsert_entity(
                entity=EntityData(
                    name=entity.name,
                    entity_type=entity.type,
                    description=entity.description,
                ),
                source=source,
            )

        # 关系边
        for relation in ca.relations:
            await self._neo4j.upsert_relation(
                relation=RelationData(
                    from_name=relation.from_name,
                    to_name=relation.to_name,
                    relation_type=relation.type,
                    description=relation.description,
                ),
                source=source,
            )

    # L3: file_relations → Document↔Document 边
    if file_relations:
        await self._write_file_relations(doc_id, file_relations, session)
```

- [ ] **Step 4: 新增 _write_file_relations 方法**

```python
async def _write_file_relations(
    self, doc_id: str, file_relations: list, session
) -> None:
    """解析 file_relations 并写入 Document↔Document 边。"""
    for fr in file_relations:
        target_title = fr.related_doc_title
        if not target_title:
            continue

        # 通过 Postgres 按 title 查找目标文档
        from sqlalchemy import select
        from src.db.models import Document

        result = await session.execute(
            select(Document.id).where(Document.title == target_title).limit(1)
        )
        target_doc = result.scalar_one_or_none()

        if target_doc is None:
            logger.info(
                f"file_relation 目标不存在: {target_title}, 跳过"
            )
            continue

        await self._neo4j.create_doc_relation(
            source_doc_id=doc_id,
            target_doc_id=str(target_doc),
            relation_type=fr.type,
            reason=fr.reason,
        )
        logger.info(
            f"file_relation: {doc_id} → {target_doc} ({fr.type})"
        )
```

- [ ] **Step 5: 更新 reindex_document**

`reindex_document` 方法（line 136-211）也需要适配新流程。将 Neo4j 更新部分改为：

```python
                # 重新分块 + 逐 chunk 分析
                chunks = chunk_text(new_text)
                chunk_analyses = []
                for chunk in chunks:
                    ca = await analyzer.analyze_chunk(chunk.text, title, chunk.index)
                    chunk_analyses.append(ca)

                # ... (embedding + Postgres 部分保持不变) ...

                # 更新 overview
                doc_analysis = await analyzer.analyze_overview(new_text, title)

                # 更新 Neo4j
                await self._neo4j.upsert_document_node(
                    doc_id=str(doc_id),
                    title=title,
                    file_type=doc.file_type,
                    overview=doc_analysis.overview,
                )
                # 写入 chunk 级实体和关系
                source_base = EntitySource(doc_id=str(doc_id), chunk_index=0, doc_title=title)
                for ca in chunk_analyses:
                    source = EntitySource(
                        doc_id=str(doc_id), chunk_index=ca.chunk_index, doc_title=title
                    )
                    for entity in ca.entities:
                        await self._neo4j.upsert_entity(
                            entity=EntityData(name=entity.name, entity_type=entity.type, description=entity.description),
                            source=source,
                        )
                    for relation in ca.relations:
                        await self._neo4j.upsert_relation(
                            relation=RelationData(from_name=relation.from_name, to_name=relation.to_name, relation_type=relation.type, description=relation.description),
                            source=source,
                        )
```

- [ ] **Step 6: 验证 Pipeline imports**

Run: `cd /Users/caoyurui/study/team-knowledge-base && uv run python -c "from src.pipeline.pipeline import Pipeline; print('Pipeline imports OK')"`

Expected: `Pipeline imports OK`

- [ ] **Step 7: Commit**

```bash
git add src/pipeline/pipeline.py
git commit -m "refactor: pipeline — 逐 chunk 图谱写入 + file_relations"
```

---

### Task 4: Search Layer — graph_enrich + related_docs

**Files:**
- Modify: `src/core/search.py`
- Modify: `src/core/knowledge_base.py`

**Interfaces:**
- Consumes: `find_entities_by_source`, `get_related_docs` from Task 1
- Produces: updated `SearchResult` with `related_docs`

- [ ] **Step 1: 扩展 SearchResult**

在 `src/core/search.py` 中更新：

```python
@dataclass
class RelatedDoc:
    doc_id: str
    title: str
    relation_type: str
    reason: str


@dataclass
class SearchResult:
    chunks: list[SearchChunk] = field(default_factory=list)
    related_entities: list[dict] = field(default_factory=list)
    related_docs: list[dict] = field(default_factory=list)
```

- [ ] **Step 2: 重写 graph_enrich**

替换现有 `graph_enrich` 函数（line 121-144）：

```python
async def graph_enrich(
    neo4j: Neo4jClient,
    survivors: list[dict],
    hops: int = 2,
) -> list[GraphQueryResult]:
    """从存活 chunks 的 (doc_id, chunk_index) 查找关联实体。

    通过 Entity.sources 属性反查哪些实体来自这些 chunk，
    然后遍历实体关系。
    """
    all_entities: dict[str, GraphQueryResult] = {}

    for c in survivors:
        doc_id = c["doc_id"]
        # 从 doc_uri 提取 chunk_index（格式: "doc_id:chunk_index"）
        chunk_index = c.get("chunk_index", 0)

        entities = await neo4j.find_entities_by_source(doc_id, chunk_index)
        for entity in entities:
            if entity.name not in all_entities:
                details = await neo4j.get_entity_details(entity.name)
                if details:
                    all_entities[entity.name] = details
                else:
                    all_entities[entity.name] = entity

    return list(all_entities.values())
```

- [ ] **Step 3: 更新 full_search 返回 related_docs**

在 `full_search` 函数中，图谱增强后追加 related_docs 查询：

```python
async def full_search(
    session: AsyncSession,
    neo4j: Neo4jClient,
    query: str,
    top_k: int = DEFAULT_TOP_K,
    threshold: float = RERANKER_THRESHOLD,
    top_n: int = RERANKER_TOP_N,
) -> SearchResult:
    # 第一层：向量粗筛
    candidates = await vector_search(session, query, top_k)

    # 第二层：Reranker 守门
    survivors = reranker_filter(query, candidates, threshold, top_n)

    # 第三层：图谱增强
    related_entities = await graph_enrich(neo4j, survivors)

    # 构造 chunks
    chunks = [
        SearchChunk(
            doc_id=c["doc_id"],
            title=(
                c.get("doc_uri", "").split(":", 1)[-1]
                if ":" in c.get("doc_uri", "")
                else ""
            ),
            chunk_text=c["chunk_text"],
            reranker_score=c["reranker_score"],
            vector_score=c["score"],
        )
        for c in survivors
    ]

    entity_dicts = [
        {"name": e.name, "type": e.entity_type, "relations": e.relations}
        for e in related_entities
    ]

    # 查询关联文档
    doc_ids = list({c["doc_id"] for c in survivors})
    related_docs = await neo4j.get_related_docs(doc_ids)

    return SearchResult(
        chunks=chunks,
        related_entities=entity_dicts,
        related_docs=related_docs,
    )
```

- [ ] **Step 4: 更新 vector_search 返回 chunk_index**

在 `vector_search` 函数的 select 中追加 `Chunk.chunk_index`：

```python
    stmt = (
        select(
            Chunk.id.label("chunk_id"),
            Chunk.chunk_index,
            Chunk.chunk_text,
            Chunk.overview,
            Chunk.doc_uri,
            Chunk.doc_id,
            (1 - Chunk.embedding.cosine_distance(query_embedding)).label("score"),
        )
        .order_by(Chunk.embedding.cosine_distance(query_embedding))
        .limit(top_k)
    )
```

并在返回的 dict 中包含 `"chunk_index": row.chunk_index`。

- [ ] **Step 5: 验证 search imports**

Run: `cd /Users/caoyurui/study/team-knowledge-base && uv run python -c "from src.core.search import SearchResult, RelatedDoc; print(f'SearchResult fields: chunks, related_entities, related_docs={hasattr(SearchResult, \"related_docs\")}'); print('OK')"`

Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add src/core/search.py src/core/knowledge_base.py
git commit -m "feat: search — sources 溯源查询 + related_docs"
```

---

### Task 5: API 层适配 — REST + MCP

**Files:**
- Modify: `src/api/routes.py:117-137`
- Modify: `src/api/mcp_server.py:36-60`

**Interfaces:**
- Consumes: `SearchResult.related_docs` from Task 4
- Produces: updated API responses

- [ ] **Step 1: 更新 REST /search 响应**

修改 `src/api/routes.py` 的 search 端点（line 125-137）：

```python
    return {
        "chunks": [
            {
                "doc_id": c.doc_id,
                "title": c.title,
                "chunk_text": c.chunk_text,
                "reranker_score": c.reranker_score,
                "vector_score": c.vector_score,
            }
            for c in result.chunks
        ],
        "related_entities": result.related_entities,
        "related_docs": result.related_docs,
    }
```

- [ ] **Step 2: 更新 MCP search tool 响应**

修改 `src/api/mcp_server.py` 的 search tool（约 line 48-60）：

```python
        return {
            "chunks": [
                {
                    "doc_id": c.doc_id,
                    "title": c.title,
                    "chunk_text": c.chunk_text[:1000],
                    "reranker_score": c.reranker_score,
                    "vector_score": c.vector_score,
                }
                for c in result.chunks
            ],
            "related_entities": result.related_entities,
            "related_docs": result.related_docs,
        }
```

- [ ] **Step 3: 验证 API 层 imports**

Run: `cd /Users/caoyurui/study/team-knowledge-base && uv run python -c "from src.api.routes import router; from src.api.mcp_server import mcp; print('API imports OK')"`

Expected: `API imports OK`

- [ ] **Step 4: Commit**

```bash
git add src/api/routes.py src/api/mcp_server.py
git commit -m "feat: API — search 响应增加 related_docs"
```

---

### Task 6: 端到端验证

**Files:** 无新增文件，验证现有功能

- [ ] **Step 1: 清理 Neo4j 旧数据**

Run: `uv run python -c "
from neo4j import GraphDatabase
d = GraphDatabase.driver('bolt://localhost:7687', auth=('neo4j','password'))
with d.session() as s:
    s.run('MATCH (n) DETACH DELETE n')
    print('Neo4j cleared')
d.close()
"`

Expected: `Neo4j cleared`

- [ ] **Step 2: 启动后端服务**

Run: `cd /Users/caoyurui/study/team-knowledge-base && uv run uvicorn src.main:app --host 127.0.0.1 --port 8001`

Expected: `Uvicorn running on http://127.0.0.1:8001`

- [ ] **Step 3: 上传两个有关联的测试文档**

上传文档 A（停车场管理），等 pipeline 完成后上传文档 B（物业制度，提到停车场）。验证：
1. 每个文档的 chunk 级实体是否正确提取
2. 同名实体是否合并（sources 包含多个 doc）
3. file_relations 是否创建 Document↔Document 边

```bash
# 上传文档 A
curl -s -X POST http://127.0.0.1:8001/documents/upload \
  -F "file=@test-doc-a.md" | python3 -m json.tool

# 等待 pipeline 完成（~15s）
sleep 15

# 检查实体
curl -s http://127.0.0.1:8001/graph/entity/张伟 | python3 -m json.tool
```

Expected: Entity 的 sources 包含 doc_a 的 chunk 信息

- [ ] **Step 4: 验证搜索返回 related_docs**

```bash
curl -s -X POST http://127.0.0.1:8001/search \
  -H "Content-Type: application/json" \
  -d '{"query":"停车场收费标准"}' | python3 -m json.tool
```

Expected: 响应包含 `chunks`, `related_entities`, `related_docs` 三个字段

- [ ] **Step 5: 验证不相关查询仍返回空**

```bash
curl -s -X POST http://127.0.0.1:8001/search \
  -H "Content-Type: application/json" \
  -d '{"query":"今天天气怎么样"}' | python3 -c "
import sys,json; d=json.load(sys.stdin)
print(f'chunks={len(d[\"chunks\"])}, entities={len(d[\"related_entities\"])}, docs={len(d[\"related_docs\"])}')
assert len(d['chunks']) == 0
print('PASS')
"
```

Expected: `chunks=0, entities=0, docs=0` + `PASS`

- [ ] **Step 6: 清理测试数据 + Commit**

```bash
# 删除测试文件
rm -f test-doc-a.md test-doc-b.md

git add -A
git commit -m "test: 三层知识图谱端到端验证通过"
```
