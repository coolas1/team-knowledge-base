# Team Knowledge Base — 项目完整技术总结(面试深挖版)

> 定位:GraphRAG 团队知识库系统 —— 纵向文档版本迭代管理 + 横向修改传播
> 分支:feat/versioned-documents(12 个提交,+2522/-31 行,23 个文件)
> 当前线上规模:18 篇文档 / 22 chunks / 16 memory_units / Neo4j 131 节点 177 边 / 227 个后端测试

---

## 0. 电梯陈述(30 秒版)

"我做了一个基于 GraphRAG 的团队知识库:文档入库后自动抽取实体关系构建三层知识图谱(Postgres+pgvector 存向量、Neo4j 存图谱),检索走'向量粗筛 → Reranker 守门 → 图谱增强'两段漏斗,通过 CLI/MCP/Web UI 三种方式访问,LLM agent 用工具协议自主检索问答。在此基础上我实现了研究目标的双轴:**纵向**——文档版本迭代管理,同名上传/编辑自动挂版本链,LLM 生成结构化版本 diff,检索默认只命中当前版;**横向**——用户提出修改要求时,agent 先定位受影响片段、生成编辑提议、做跨文档一致性检查,确认后落库为新版本。还包括改名文件的版本识别(内容指纹三级判定)。"

---

## 1. 系统全貌

### 1.1 三模块架构

```
src/
├── engine/     GraphRAG 引擎(本项目的核心)
│   ├── cli.py / mcp.py / interface.py    入口与协议
│   ├── components/
│   │   ├── extractors/   按格式抽取(pdf/docx/pptx/markdown/OCR)
│   │   ├── chunker.py    分块
│   │   ├── analyzer.py   LLM 分析(overview/实体/关系/版本diff/编辑提议)
│   │   ├── embedder.py   向量化(nomic-embed-text, 768维)
│   │   ├── reranker.py   重排(local torch / http API / none 三模式)
│   │   └── store/        postgres.py(权威存储) + neo4j.py(图谱) + models.py
│   ├── graphrag/         pipeline.py(入库编排) + backend.py(实现 KnowledgeBase 协议)
│   │                     + _search.py(检索漏斗) + _version_match.py(改名识别)
│   └── hindsight_components/  第二条记忆管线(recall/reflect + Neo4j 投影 worker)
├── agent/      无状态技能层(engine_client 双实现:in-process / MCP)
└── frontend/   FastAPI BFF + React 19 SPA
```

外加 `src/extensions/pi-agent/`(TypeScript agent 运行时,通过 MCP 调 engine 工具)。

### 1.2 技术栈与选型理由

| 组件 | 选型 | 为什么 |
|---|---|---|
| 向量库 | Postgres + pgvector(HNSW,cosine) | 与业务数据同库,事务一致,免运维独立向量库 |
| 图谱 | Neo4j 5 | 实体关系 MERGE 聚合、Cypher 变长路径查询 |
| Embedding | Ollama + nomic-embed-text | 本地部署、768 维、零 API 成本 |
| LLM | glm-5.3(ark API,OpenAI 兼容) | 推理模型,质量/成本平衡 |
| Agent 协议 | MCP (streamable HTTP) | 工具自描述,pi-agent/Claude 等客户端即插即用 |
| 部署 | docker compose 五容器 | postgres/neo4j/ollama/webapp/pi-agent |

### 1.3 关键协议分层(面试常问"怎么解耦的")

```
KnowledgeBase Protocol (src/engine/interface.py)   ← 引擎契约
    ↑ 实现
GraphRAGBackend                                     ← 具体引擎
    ↑ 包装
HindsightKnowledgeBaseAdapter                       ← 记忆状态增强装饰器
    ↑ 两种实现
InProcessEngineClient / McpEngineClient             ← agent 侧统一客户端
    ↑
BFF 路由 / pi-agent / CLI / MCP server              ← 四种消费端
```

**教训**(真实 bug):每加一个 KnowledgeBase 方法,必须同步改 5 处——
backend 实现、interface 协议、hindsight adapter 透传(它手写代理每个方法)、
两种 EngineClient、MCP 工具注册。漏掉 adapter 就 500(版本工具当时就是这样暴露的)。

---

## 2. 核心数据流

### 2.1 入库管线(pipeline.process_file)

```
文件字节 → content_hash(SHA256,幂等判断)
  → registry.extract(按扩展名选抽取器;OCR 走 tesseract)
  → chunker.chunk_text(先分块)
  → analyzer.analyze_overview(文档级:摘要 + 跨文档关联推测)
  → analyzer.analyze_chunk × N(chunk 级:实体/关系抽取)
  → embedder.embed_batch(chunks 向量化)
  → Postgres: chunks 表(带 embedding)+ documents 更新
  → Neo4j: 三层图谱写入(见 2.2)
  → [有版本上下文时] 版本 diff 抽取 + 版本图谱投影
  → index_hook.after_indexed(Hindsight 二级索引,失败不影响主流程)
```

失败语义:任何一步异常 → status='failed' + error_msg,**版本元数据失败例外**——
`_process_version_change` 内部 try/except,只记日志不回滚文档 indexed 状态
(版本 diff 是附加产物,不能拖垮主索引,与 index_hook 同哲学)。

### 2.2 三层图谱(面试必考)

- **L1 chunk 级**:每个 chunk 抽取的实体/关系逐条写入
- **L2 文档内聚合**:实体节点 `MERGE by name` 全局唯一,`sources` 属性(JSON 字符串数组)累积溯源 `[{doc_id, chunk_index, doc_title}]` —— 同名实体跨文档自然聚合,这是 LightRAG 式增量合并的关键
- **L3 跨文档**:LLM 在 overview 分析时推测 file_relations(REFERENCES/SAME_TOPIC/ANALYZES),按 title 反查目标文档写 `RELATED_TO` 边

**为什么 sources 用 JSON 字符串不用 list<map>?** Neo4j 属性不支持 list of maps。代价:清理和过滤都要 `CONTAINS doc_id` 子串匹配——因为 doc_id 是 UUID(36 字符定长),子串误匹配概率可忽略。

### 2.3 双检索管线(最容易"被问穿"的地方)

系统有**两条独立的检索实现**,这是历史架构(引擎检索 + Hindsight 记忆检索)造成的:

```
路径 A(GraphRAG): full_search
  vector_search(pgvector top-K,JOIN documents 过滤 is_current)
  → reranker_filter(CrossEncoder 阈值 0.01 守门, top-N 10)
  → graph_enrich(命中 chunk 的实体 + 关联文档)

路径 B(Hindsight): recall
  memory_units 上的 semantic_search / keyword_search / entity_search
  → RRF 融合 → MMR 多样性选择
  → reflect 策略时做反思推理
```

**真实踩坑**:我给版本管理加"只检索当前版"过滤时,先只改了路径 A 的
`vector_search`,端到端验证发现旧版本内容照样出现在搜索结果——排查后发现
`/api/search` 实际走的是路径 B(Hindsight),它的 **5 个查询路径**
(semantic/keyword/entity/temporal + 状态过滤)全都只查 `status='indexed'`。
最终在 `hindsight_components/repository.py` 里 5 处全部补上
`Document.is_current.is_(True)`。

**面试话术**:"这个 bug 教会我,在多管线架构里改横切行为(检索域)必须先做调用链审计,单测 mock 根本暴露不了这种集成缝隙。"

### 2.4 MCP 工具面(14 个)

search / query_knowledge / search_fast / search_deep / get_document /
query_graph / upload_document / list_documents / remove_document /
get_full_graph + 版本五件套:
- `tkb_list_versions(doc_id)` — 版本链 + 每版变更摘要
- `tkb_diff_versions(doc_id, from, to)` — 结构化 diff
- `tkb_edit_document(doc_id, new_text)` — 版本化编辑落库
- `tkb_propose_edit(doc_id, edit_request)` — 生成编辑提议(不落库)
- `tkb_confirm_version_match(doc_id, parent_doc_id)` — 改名候选确认挂链

---

## 3. 纵向:版本迭代管理(Phase 1)

### 3.1 数据模型(面试画图题)

```sql
-- documents 增 4 列(幂等迁移)
version_group UUID     -- 逻辑文档身份,同组 = 同一文档的各版本(旧数据回填 = 自身 id)
version_number INT     -- 组内版本号从 1 递增
version_of UUID        -- 上一版 doc_id(自引用 FK, SET NULL)
is_current BOOLEAN     -- 组内最新版标记(检索域)

-- 新表
document_changes(
  id, doc_id FK CASCADE,   -- 指向新版本行
  from_version, to_version,
  summary TEXT,            -- LLM 一句话变更摘要
  changes JSONB            -- [{name, description, status: added|removed|modified}]
)
```

**核心设计决策:版本即文档行,不是独立版本表。** 理由:复用 chunks 的
doc_id 外键、删除级联、Neo4j 投影管线——迁移成本最低。VersionRAG 用独立
Version 节点因为它以图为中心;本项目 Postgres 是权威存储,行级建模更自然。

**为什么"退位不删除"(is_current=false 而非删行)?** Graphiti 的边失效思想:
历史是特性不是垃圾——支撑追溯、审计、未来回滚。旧版全文/chunks/图谱全部保留。

**迁移怎么做的?** `Base.metadata.create_all` 不会给已存在的表加列,所以在
`init_db` 里手写 `ALTER TABLE documents ADD COLUMN IF NOT EXISTS ...` +
`UPDATE documents SET version_group = id WHERE version_group IS NULL` 回填。
老库无感升级。

### 3.2 版本链入库(backend.ingest)

```
上传 → hash 计算 → 同名(且 is_current)文档存在?
  ├─ 是 → 内容 hash 相同? → 幂等返回现有文档(不产生新版本)
  │       否则 → 挂链:新行继承 version_group,version_number=parent+1,
  │              version_of=parent.id;parent 置 is_current=false
  └─ 否 → 走改名识别(见第 5 节) → 仍无匹配则独立入库(v1)
```

版本上下文(VersionParent: 上一版 doc_id/raw_text/版本号)在 session 关闭前
提取成**纯 dataclass**,避免 ORM 对象脱离会话的陷阱。

### 3.3 LLM 版本 diff(analyzer.analyze_changes)

Prompt 设计参考 VersionRAG 的 `generate_changes_from_diff`:
- 只提取**实质变更**(字段/章节/数值/结论/定义),明确排除排版/标点/空白/页码
- 输出 `{summary, changes:[{name, description, status: added|removed|modified}]}`
- status 白名单校验,非法值归一为 modified
- 复用 `_extract_json` 容错解析(见 7.1)

实测效果(真实文档 v1→v2):返回 7 条结构化变更,精准覆盖配送范围扩大/
时间延长/取消人工配送(removed)/新增机器人试点(added)/两次价格上调。

### 3.4 Neo4j 版本投影

```
(d:Document {version_number, is_current})
(prev)-[:NEXT_VERSION]->(next)
(c:Change {from_version, to_version, summary, changes})-[:CHANGE_OF]->(d)
```

删除文档时连带清理:Change 节点 → 孤立实体(sources 清空后 DETACH DELETE)
→ Document 节点。reindex 路径注意点:delete 会拆掉 NEXT_VERSION 边,
所以 reindex 后要按 `doc.version_of` **重连版本边**。

### 3.5 检索默认最新版

- 路径 A:`vector_search` JOIN documents WHERE is_current
- 路径 B:repository 5 处查询全部加过滤(见 2.3 的教训)
- 图谱侧:`get_full_graph`/`query_neighbors`/`get_entity_details` 加"存活"
  过滤——实体/关系边的 sources 里至少有一个**当前版**文档(见 6.3)

---

## 4. 横向:编辑传播(Phase 2)

### 4.1 编辑即版本(edit_document)

前端编辑按钮调 `PUT /documents/{id}/content` ——这个接口**后端原本不存在**
(405,历史遗留;远端甚至有个 `bugfix/indexed-file-edit-405` 分支名)。
我把它实现为版本化编辑:创建 v+1 新行(继承版本链)→ 旧版退位 →
pipeline 重索引 + 补记 diff → 前端跳转新版本详情页。

幂等:内容 hash 与当前版相同 → 直接返回现有文档。

### 4.2 编辑提议管线(tkb_propose_edit,OneEdit 范式)

**核心哲学:编辑是"提议 + 验证",不是直接写。**

```
① 定位:edit_request 向量化 → 该文档 chunks 的 cosine top-3(带 relevance 分)
② 提议:LLM 生成修改后全文 + notes
   (prompt 约束:只做要求涉及的改动,其余逐字保留,保持格式;
    notes 还要求报告发现的潜在冲突)
③ 影响面:Neo4j 查共享实体的其他当前版文档(跨文档一致性检查)
→ 返回 {affected_chunks, proposed_text, notes, related_documents, next_step}
→ 用户确认 → tkb_edit_document 落库(新版本 + LLM diff)
```

实测(改配送费 5元→8元):affected chunk 定位正确(relevance 0.637)、
提议只改目标行(加急费 3 元逐字保留)、notes 3 条(含"全文仅此一处提及
该金额,加急费不受影响"的影响面判断)。

**为什么不自动落库?** 内容相似不等于同一文档;同理,LLM 的编辑提议
可能有错,把确认权留给人。这与改名识别第三级不自动挂链是同一个设计原则。

### 4.3 意图路由(版本敏感问题分流)

pi-agent 系统提示词加"版本规则"区块 + 新增 `tkb-versions` skill:
- 问版本历史 → tkb_list_versions
- 问两版区别 → tkb_diff_versions
- 要求修改 → 先 propose 后 edit
- 内容问题默认当前版;涉及历史状态先查版本链

(注:这比 VersionRAG 的 LLM 意图分类器轻量——agent 本身就是 LLM,
让工具选择发生在 agent 的 reasoning 里,不需要独立分类器。)

### 4.4 跨文档一致性检查

`find_related_docs_via_entities(doc_id, limit=5)`:Cypher 找与本文档
**共享实体**的其他当前版文档,按共享实体数排序。原理:实体节点全局
MERGE,sources 数组含多个 doc_id 即为共享。加 `is_current <> false`
过滤排除退版文档。

---

## 5. 改名识别(最常被追问的算法细节)

### 5.1 问题与三级判定

用户改内容又改文件名(`规范v1.md` → `规范_final.md`),标题匹配失效。
`_version_match.py` 三级判定:

```
第一级:纯重命名 —— content_hash(SHA256)与某当前版完全相同
        → 确定性判断,自动挂链
第二级:改名+修改 —— 加权相似度 ≥ 0.70 → 返回候选,等确认
第三级:无匹配 → 独立新文档
```

### 5.2 相似度算法

```
sim = 0.3 × 标题相似度 + 0.7 × 内容相似度

内容相似度:正文 3-gram 字符 shingle 的 Jaccard
  - 为什么 shingle 不用 embedding?零依赖/零成本/确定性/可解释,
    且上传路径要同步算,不能容忍一次 ollama 往返(刻意取舍)
  - 为什么 3-gram 字符不用词?中文无需分词,对错别字/局部改动鲁棒
标题相似度:2-gram shingle Jaccard,且先剥离版本痕迹
  (正则去 v1/final/draft/r2/日期/序号,让"报告_v2"和"报告_final"对齐)
权重:改名场景标题天然低分,证据权重必须让位内容(0.3/0.7)
```

### 5.3 阈值标定(面试最有说服力的部分)

实测五档场景:

| 场景 | 相似度 | 期望 |
|---|---|---|
| 小改 1 处(5元→8元) | 0.81 | 判为新版本 |
| 中改 30% 内容 | 0.71 | 判为新版本 |
| **同模板不同内容**(会议纪要模板) | **0.44** | 判为独立 |
| 全部重写 | 0.30 | 独立 |
| 无关文档 | 0.06 | 独立 |

**0.70 卡在修订版(≥0.71)与模板陷阱(≤0.44)之间,两侧各有 0.25+ 安全边际。**

端到端实测:上传改名+改价版 → 相似度 0.784 → 返回 version_match 候选
(doc_id/title/similarity/exact_content),未自动挂链 →
`tkb_confirm_version_match` 确认 → 挂链 v2、补 LLM diff、连 NEXT_VERSION 边。

### 5.4 已知边界(主动说出来加分)

- 大幅重写(<0.70)的改名版会漏判为独立文档——**刻意保守**:误挂链
  (把别人的文档认成你的新版本)比漏挂危害大
- 超短文档 shingle 集合太小,区分度下降(实测重复串文本 Jaccard 被压到
  0.43-0.52),建议文档下限约 50 字——这是我标定测试数据时踩的真实坑:
  第一版测试用 `"条款一"*40` 这种重复文本,相似度算出来 0.44,
  排查后发现是 shingle 集合坍缩(重复片段只贡献一个 shingle),
  换真实多样文本后标定才成立
- 未来方向:embedding 全文向量对语义改写更鲁棒,但引入推理成本

---

## 6. 图谱净化与一致性(Phase 2.5)

### 6.1 退版实体问题

版本退位后,旧版实体(及其独有实体)仍在图谱里污染
`get_full_graph`/`query_neighbors`/`query_graph`。

### 6.2 方案:存活过滤(借 Graphiti 边失效思想)

实体/关系边"存活" = sources 里至少有一个 doc_id 属于 is_current 的文档:

```cypher
AND EXISTS {
  MATCH (ld:Document)
  WHERE coalesce(ld.is_current, true) <> false   -- 关键
    AND e.sources CONTAINS ld.doc_id
}
```

**`coalesce(ld.is_current, true)` 的原因**:版本化功能上线前的存量
Document 节点没有 is_current 属性,Cypher 里 `null <> false` 是 null
(不匹配),不加 coalesce 会把所有存量实体制裁掉——灰度兼容的经典细节。

`get_entity_details` 特意放宽为 `n.sources IS NULL OR EXISTS{...}`:
Hindsight 投影的实体**没有 sources 属性**,要保留可见,只过滤 GraphRAG
实体(有 sources 的)。

### 6.3 删除的并发问题(真实生产问题)

连续快速删除版本链关联的文档:
1. Neo4j **TransientError 死锁**(两个删除 + graph worker 并发锁同批节点)
   → 重试即成功;改进方向:删除加 backoff 重试或合并事务
2. **graph worker 竞态**:Hindsight 投影 worker 处理删除事件时,
   in-flight 的 replace 事件重建了 Document 节点 → 孤儿节点
   (Postgres 权威数据已清,图谱残留)→ 手动清理;改进方向:删除事件
   优先级标记 / 投影幂等键

**面试话术**:"权威存储与投影的最终一致在删除场景最脆弱——
我遇到的孤儿节点问题本质是 outbox 事件乱序,方案是给 delete 事件
加屏障或做投影侧 tombstone。"

---

## 7. LLM 工程细节(推理模型踩坑全记录)

glm-5.3 是推理模型(reasoning model),三个连环坑,全部真实踩过:

### 7.1 JSON 解析三连击

1. **围栏**:回复包 ```json 围栏,原文按行剥离的旧逻辑在"围栏外有说明
   文字"或"围栏未闭合"时全崩 → 重写 `_extract_json`:多候选提取
   (闭合围栏 → 未闭合围栏余文 → 原文 → 最外层花括号子串)
2. **截断**:推理消耗输出预算,JSON 正文被 max_tokens 腰斩 →
   a) 请求显式设 `max_tokens: 8192`;b) `_repair_truncated_json`
   兜底:状态机扫描字符串/转义/括号栈,补齐未闭合引号与括号、
   去悬空尾逗号(能救回截断前的大部分实体)
3. **空响应**:并发时偶发 content 为空(预算全给思考)→
   `_call_openai_compatible` 空响应自动重试 3 次

### 7.2 developer role 400

pi-agent 库按 OpenAI 新规范发 `"role": "developer"`,ark 的 glm-5.3
只认 system/assistant/user/tool → 400,且 pi-agent 把错误吞了,
表现为"提问没有回复"(0.2s 返回空 answer)。排查手段:monkey-patch
`globalThis.fetch` 打日志,抓到真实请求体和 400 响应。修复:`.env` 加
`PI_AGENT_REASONING=false`(库的 useDeveloperRole 条件是
`model.reasoning && compat.supportsDeveloperRole`,关 reasoning 即回落
system 角色;模型原生思考不受影响,只改请求格式)。

### 7.3 环境变量污染

`~/.bashrc` export 的 LLM_*/NEO4J_* 优先级高于 `.env`(compose 变量
替换规则:shell env > .env),导致容器拿到内网 LLM 地址、密码错位。
修复:注释 bashrc + `.env` 与实际值对齐。**教训:基础设施调试先查
环境变量三层(shell/.env/容器 env)的一致性。**

---

## 8. 测试与验证策略

### 8.1 数字速查

- 后端 pytest:227 passed / 2 skipped(本分支新增 52 个,版本测试文件 47 个)
- 前端 vitest:8 个 api-client 测试
- 迁移:真实容器启动验证(4 列 + document_changes 表自动创建/回填)
- E2E:同名两版上传、PUT 编辑、diff API、改名候选、confirm 挂链、
  检索单一命中、图谱边验证(cypher-shell 直查)

### 8.2 单测设计模式(面试可讲)

- **Neo4j mock**:Fake Driver/Session 记录 Cypher 语句和参数,断言
  语句包含关键子句(如 `coalesce(ld.is_current, true)`)——不依赖真实库
- **分层替身**:FakeSession 记录 session.add 的 ORM 对象;
  RecordingAnalyzer/RecordingNeo4j 记录调用参数,验证编排正确性
- **不 mock 的部分**:analyzer 的纯解析函数直接用各种畸形 JSON 直测
  (围栏/截断/未知 status/非 dict 条目)
- **测试数据反例**:标定测试最初用重复串文本,相似度全错——
  换真实多样文本才对。**经验:相似度类算法的测试数据必须模拟真实分布,
  合成数据会系统性扭曲统计量**

### 8.3 端到端验证发现的 bug 清单(证明 E2E 不可替代)

| bug | 单测为何没拦住 |
|---|---|
| hindsight adapter 缺版本方法 → 500 | 协议是手写代理,mock 的 FakeKB 直接实现了方法 |
| hindsight 5 路径检索缺 is_current → 旧版泄漏 | 两条检索管线独立,单测各测各的 |
| pi-agent 吞 400 → 空回复 | 库内部行为,应用层测试覆盖不到 |
| Neo4j 删除死锁/孤儿节点 | 并发时序,功能测试串行执行 |

---

## 9. 面试追问预案(Q&A)

**Q: 为什么不用 Git 管理文档版本?**
A: Git 管字节序列,不懂语义。我们要的是 LLM 可消费的结构化 diff
(added/removed/modified 条目)、实体级影响分析、以及与检索域
(is_current)的联动——这些都是应用层语义,Git 给不了。
当然,反过来说,如果用户场景是代码/配置文件,Git 是对的;我们的场景是
办公文档(pdf/pptx/中文 markdown)。

**Q: 相似度为什么不用 embedding?**
A: 三点:①上传是同步路径,shingle 零延迟零成本,embedding 要一次
ollama 往返;②确定性可解释,阈值可标定可审计,embedding 相似度
受模型/维度影响会漂移;③改名检测需要的是"文本重叠"信号而不是
"语义相似"——两份同主题不同文档 embedding 也很像(0.4x 的模板陷阱
在 embedding 空间里可能更高)。代价是对同义改写不鲁棒,已列为演进方向。

**Q: 版本链为什么不用链表式 next 指针而用 version_of(父指针)?**
A: 版本只追加不重排,父指针天然表达"我修改自谁";取全链一条
`WHERE version_group = X ORDER BY version_number` 即可,不需要遍历。
Neo4j 侧反而存 NEXT_VERSION 边(子指针),因为图查询方向灵活,
两种表示各取所长。

**Q: document_changes 为什么按 doc_id 幂等覆盖(先 delete 再 insert)?**
A: 重试/确认重复时不能产生重复变更记录;`(doc_id, from_version)`
唯一约束兜底,业务代码先删后插保持简单。

**Q: 如果两个用户并发上传同名文件?**
A: 当前实现 select-then-insert 存在竞态窗口(都查到无 parent → 各自成 v1)。
危害低(产生两个组,后续 confirm 可合并)。正确做法:documents 加
`(version_group, version_number)` 部分唯一索引 + ON CONFLICT 重试。
这是已知局限,诚实说出来并给出方案比假装没有强。

**Q: LLM diff 的成本?**
A: 每次 2 次调用(overview + 各 chunk)+ 新版时 1 次 diff 调用;
diff 输入 8000 字截断,输出 max_tokens 8192,glm-5.3 实测每次
约 1-3k completion tokens。索引期成本 ≈ GraphRAG 常规入库 + ~15%。

**Q: 系统瓶颈在哪?**
A: 入库管线是 LLM 密集(每 chunk 一次分析),大文档分钟级;
检索路径 B(Hindsight)每次 query 都全量 RRF;图谱存活过滤的
EXISTS 子查询在实体多时是 O(E×D)——数据量上来后应物化
"存活 sources"或在退版时重写实体 sources(当前选择查询时过滤,
因为退版是低频操作,读多写少)。

**Q: 这个项目和 Microsoft GraphRAG 的区别?**
A: MS GraphRAG 是社区检测+全局摘要的离线索引,每次更新全量重算;
我们是增量 MERGE(实体 sources 聚合)+ 版本感知 + 编辑传播,
且检索是两段漏斗(reranker 守门)不是图遍历优先。

**Q: 最大的技术风险?**
A: LLM 抽取质量决定图谱质量(0 实体问题我们真实遇到过);
单点依赖推理模型的行为怪癖(developer role/空响应/截断);
以及双管线架构的一致性税——每加横切能力要改 N 处,
长期应该收敛成单一检索抽象。

---

## 10. 局限与演进(主动交底)

1. **并发安全**:ingest 的 select-then-insert 竞态(见 Q&A)、
   删除死锁重试、投影事件乱序 —— 三个并发问题都有明确修复方案未做
2. **改名识别对语义改写不鲁棒**:同义重写 < 0.70 会漏判(保守取舍)
3. **意图路由是提示词级**:未做独立的版本意图分类器和量化评测
   (VersionQA 式中文基准是自然的下一步)
4. **横向传播只到"检查报告"**:确认后不自动修改关联文档
   (OneEdit 式自动传播 + 人工审核队列是演进方向)
5. **前端版本 UI 最小化**:列表 v{n} 徽标 + 详情版本历史,无版本对比视图

## 11. 一页速查(面试前 5 分钟看这个)

- 栈:FastAPI BFF + React 19 SPA + MCP + pi-agent(TS);
  Postgres/pgvector(HNSW cosine) + Neo4j 5 + Ollama(nomic-embed-text 768d);
  glm-5.3 via ark
- 管线:extract → chunk → analyze(overview+chunk) → embed → 写库 → 图谱
- 图谱三层:L1 chunk / L2 MERGE-by-name+sources 聚合 / L3 file_relations
- 检索漏斗:vector top-20 → reranker(阈值 0.01, top-10)→ graph enrich
- 版本:version_group/number/of/is_current + document_changes;
  退位不删;diff = LLM 结构化(added/removed/modified)
- 改名识别:hash(自动)→ 0.3×标题+0.7×内容 shingle Jaccard ≥0.70(候选待确认)
- 编辑:propose(定位+提议+影响面)→ confirm → edit(新版本)
- 关键数:阈值 0.70(修订≥0.71 / 模板≤0.44)/ 227 tests / 12 commits / +2522 行
- 最深坑:双管线检索域不一致、developer-role 400、JSON 截断修复、
  Neo4j 删除死锁
