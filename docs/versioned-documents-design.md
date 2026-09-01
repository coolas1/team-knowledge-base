# 版本化文档管理设计与实现

> 分支:`feat/versioned-documents`(自 `multi-file-upload` 切出)
> 日期:2026-08-28
> 范围:纵向版本迭代管理(Phase 1)+ 版本化编辑(Phase 2 首块)

---

## 1. 研究目标

用户提出修改要求时:

- **横向**:联动更新知识图谱相关内容与关联文档(Phase 2+,未完成)
- **纵向**:进行文档版本迭代管理,保留全部历史版本、追踪版本间变更、
  默认检索只命中当前版(本分支完成)

## 2. 论文与开源依据

### 2.1 VersionRAG -- 版本感知检索(本方案的主要蓝本)

**出处**:
Huwiler, D., Stockinger, K., & Fürst, J. (2025).
*VersionRAG: Version-Aware Retrieval-Augmented Generation for Evolving Documents.*
arXiv:2510.08109. https://arxiv.org/abs/2510.08109
开源实现:https://github.com/danielhuwiler/versionrag (MIT,研究原型)

**核心思想与借鉴点**:

| VersionRAG 机制 | 本项目落地 |
|---|---|
| 层次图结构 `(Documentation)-[:HAS_VERSION]->(Version)-[:HAS_CONTENT]->(Content)` | `documents` 表每行即一个版本;`version_group` 承担 Documentation 的逻辑文档身份 |
| `[:NEXT_VERSION]` 版本链边 | Neo4j `(d:Document)-[:NEXT_VERSION]->(d2:Document)` |
| LLM diff 抽取结构化变更(过滤排版/标点等非实质差异) | `analyzer.analyze_changes()`,prompt 直接参考其 `generate_changes_from_diff` 设计 |
| 检索时意图分类路由(Version/Change/Content 三种路径) | Phase 1 简化为默认只检索当前版;版本查询走显式 MCP 工具(完整意图路由属 Phase 2) |
| 版本敏感问答准确率 90%(naive RAG 58%, GraphRAG 64%) | 佐证"显式建模版本"路线的有效性 |

**VersionRAG 实测数据引用自论文摘要**:34 篇版本化技术文档、100 个人工
标注问题的 VersionQA 基准;索引 token 开销比 GraphRAG 低 97%。

### 2.2 Zep/Graphiti -- 双时序知识图谱(图谱侧参考)

**出处**:
Rasmussen, P., Paliychuk, P., Beauvais, T., Ryan, J., & Chalef, D. (2025).
*Zep: A Temporal Knowledge Graph Architecture for Agent Memory.*
https://www.getzep.com/(论文 PDF)
开源实现:https://github.com/getzep/graphiti (Apache-2.0)

**核心思想与借鉴点**:

| Graphiti 机制 | 本项目落地 |
|---|---|
| 边失效(edge invalidation):新事实与旧事实矛盾时设 `invalid_at`,**不删除** | 旧版本退位用 `is_current=false` 标记而非删除,图谱与全文全部保留 |
| 双时序建模(`t_valid/t_invalid` + `t_created/t_expired`) | 版本号(`version_number`/`from_version`/`to_version`)作为时序锚点;实体级边失效标注属 Phase 2 |
| `resolve_edge_contradictions` 的时间区间判定算法 | 后续实体级 diff 时参考其区间重叠判定 |

### 2.3 辅助参考

- **LightRAG** (Guo et al., 2024, arXiv:2410.05779,
  https://github.com/HKUDS/LightRAG):增量并集合并、按 doc_id 选择性删除
  保留共享实体——本项目 pipeline 的 MERGE 聚合与其同思路。LightRAG
  **没有版本概念**(重复上传静默跳过),这是本方案与它的本质差异。
- **OneEdit** (Zhang et al., 2024,神经-符号协作知识编辑):"编辑是
  提议+验证,不是直接写"——Phase 2 横向传播的交互范式依据。

## 3. 原方案 vs 新方案

### 3.1 原方案(multi-file-upload 及之前)

```
documents(id, title, raw_text, overview, content_hash, status, ...)
   └─ chunks(doc_id, chunk_index, chunk_text, embedding, ...)

行为:
- 上传同名文档 -> 当作全新文档独立入库(新旧共存,搜索双命中)
- 无版本概念、无变更追踪、无历史保留语义
- 前端编辑按钮调 PUT /documents/{id}/content -> 405(接口从未实现)
- 检索:所有 chunk 一律参与(旧内容污染搜索结果)
- Neo4j:Document 节点仅 title/file_type/overview
```

### 3.2 新方案(feat/versioned-documents)

```
documents(id, title, ...,                        ← 原字段
          version_group,   ← 新:逻辑文档身份(同组 = 同一文档的各版本)
          version_number,  ← 新:组内版本号,从 1 递增
          version_of,      ← 新:上一版 doc_id(自引用 FK)
          is_current)      ← 新:组内最新版标记(检索域)
   └─ chunks(原样)

document_changes(                                 ← 新表
   id, doc_id FK, from_version, to_version,
   summary,        ← LLM 一句话变更摘要
   changes)        ← JSONB: [{name, description, status: added|removed|modified}]

Neo4j 投影(新增):
   (d:Document {version_number, is_current})
   (prev)-[:NEXT_VERSION]->(next)
   (c:Change {from_version, to_version, summary, changes})-[:CHANGE_OF]->(d)
```

### 3.3 行为差异对照

| 场景 | 原方案 | 新方案 |
|---|---|---|
| 上传同名文档 | 新文档独立入库,两版共存,无关联 | 自动挂入版本链:版本号 +1,继承 version_group,旧版退位 |
| 上传完全相同内容 | 重复入库 | 幂等跳过,返回现有文档 |
| 编辑保存 | **405 报错**(接口不存在) | 版本化编辑:生成 v+1 新行,旧版保留,自动记 diff |
| 版本间变更 | 无 | LLM 结构化 diff(过滤格式噪音),存表 + 图谱 |
| 搜索 | 新旧版本同时命中(过期信息污染) | 只命中 `is_current` 的版本 |
| 查历史版本 | 无入口 | `tkb_list_versions` / `tkb_diff_versions` MCP 工具 + BFF 路由 |
| 删除文档 | 删当前 | 连带清理版本链边 + Change 节点(旧版数据级联) |

## 4. 项目结构(本分支改动)

```
src/
├── engine/
│   ├── components/
│   │   ├── analyzer.py          +88  analyze_changes():LLM diff(VersionRAG 式 prompt)
│   │   │                              _parse_changes_response() 复用容错解析
│   │   └── store/
│   │       ├── models.py        +60  Document 4 个版本列;新表 DocumentChange
│   │       ├── postgres.py      +27  init_db 幂等迁移(ALTER TABLE + 回填 version_group=id)
│   │       └── neo4j.py         +95  link_next_version / upsert_changes /
│   │                                get_version_chain;delete 清理 Change;Document 节点带版本属性
│   ├── graphrag/
│   │   ├── backend.py          +239  ingest 版本链检测;edit_document() 版本化编辑;
│   │                                list_versions() / diff_versions()
│   │   ├── pipeline.py         +120  VersionParent 上下文;_process_version_change():
│   │                                diff 入库 + 图谱投影(失败不影响文档 indexed)
│   │   └── _search.py            +8  vector_search join documents 过滤 is_current
│   ├── hindsight_components/
│   │   ├── adapter.py           +13  透传 list_versions / diff_versions / edit_document
│   │   └── repository.py         +5  5 个检索路径全部加 is_current 过滤
│   ├── interface.py              +9  KnowledgeBase / DocumentRef 协议扩展
│   └── mcp.py                   +50  tkb_list_versions / tkb_diff_versions / tkb_edit_document
├── agent/
│   ├── interface.py              +5  EngineClient 协议扩展
│   └── engine_client.py         +34  本地 + MCP 双实现
└── frontend/webapp/
    ├── server/routes_documents.py +41  GET /{id}/versions、/versions/diff、PUT /{id}/content
    └── client/src/
        ├── api/client.ts        +46  DocumentVersion/Diff 类型;listVersions/diffVersions
        └── pages/DocumentListPage.tsx +16  v{n} 徽标(非当前版置灰)
           pages/DocumentDetailPage.tsx +63  版本历史区块;保存后跳转新版本

tests/engine/test_versioning.py +405  22 个单元测试(解析/投影/编排/协议)
```

共 19 个文件,+1296/-31 行,8 个提交(每步可独立回退)。

## 5. 关键设计决策

1. **版本即文档行**(非单独版本表):复用现有 chunks 外键、删除级联、
   Neo4j 投影管线,迁移成本最低;VersionRAG 用独立 Version 节点是因为
   它以图为中心,本项目以 Postgres 为权威存储,行级建模更自然。
2. **退位而非删除**(Graphiti 思想):旧版 `is_current=false` 后仍完整
   保留全文/chunks/图谱,支撑历史追溯与回滚(未来功能)。
3. **幂等迁移**:`create_all` 不给已存在的表加列,`init_db` 里
   `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` + `version_group` 回填,
   老库无感升级。
4. **版本元数据失败隔离**:LLM diff 属附加产物,`_process_version_change`
   异常只记日志,不回滚文档本身的 indexed 状态(与 index_hook 同哲学)。
5. **检索域收缩要在两条管线同时做**:GraphRAG 的 `vector_search` 与
   Hindsight 的 5 个检索路径是独立实现,只改一处会导致旧版泄漏——
   这是端到端验证发现的真实 bug(两个 fix 提交的来源)。
6. **改名识别采用分级判定 + 人工确认**(详见 5.1 节):标题匹配失效时
   用内容指纹兜底,但"相似"只产生确认提议而非自动挂链——内容相似
   不等于同一文档(模板文档、同主题文档是天然的误报源)。

### 5.1 改名识别:如何判定"改名后的修改版"是同一文件的新版本

**问题**:用户改了内容又改了文件名(`规范v1.md` -> `规范_final.md`),
标题精确匹配失效,新上传会被当作两个不相关的独立文档。

**方案**(`src/engine/graphrag/_version_match.py`,三级判定):

```
上传新文档(标题 != 任何当前版文档)
  │
  ├─ 第一级:纯重命名检测
  │    content_hash (SHA256) 与某个当前版完全相同
  │    → 文件只是改了名,内容一字未动
  │    → 自动挂入该文档的版本链(确定性判断,无需确认)
  │
  ├─ 第二级:内容相似度检测(改名 + 修改)
  │    sim = 0.3 × 标题相似度 + 0.7 × 内容相似度
  │    - 标题相似度:剥离版本痕迹(v1/final/日期/序号)后
  │      的字符 shingle Jaccard
  │    - 内容相似度:正文 3-gram 字符 shingle 的 Jaccard
  │      (对中文友好,无需分词,无外部依赖)
  │    sim ≥ 0.70 → 返回 version_match 候选
  │
  └─ 第三级:无匹配
       → 按全新独立文档入库(原行为)
```

**为什么阈值是 0.70**——实测标定:

| 场景 | 加权相似度 | 期望判定 |
|---|---|---|
| 小改 1 处(5元→8元) | 0.81 | ✅ 新版本 |
| 中改 30% 内容 | 0.71 | ✅ 新版本 |
| **同模板不同内容**(会议纪要模板等) | **0.44** | ❌ 独立文档 |
| 全部重写 | 0.30 | ❌ 独立文档 |
| 无关文档 | 0.06 | ❌ 独立文档 |

0.70 恰好卡在"真实修订版"(≥0.71)与"模板陷阱"(≤0.44)之间,
两侧都有约 0.25+ 的安全边际。权重上标题只占 0.3(改名场景标题
相似度天然低,证据权重必须让位于内容)、内容占 0.7。

**为什么第二级只提议不自动挂**:内容相似度无法区分"同一文档的
修订"与"基于同一模板写的新文档"——两者内容都高度相似。这是
原理性限制,不是阈值问题。所以设计为:返回
`version_match: {doc_id, title, similarity, exact_content}` 候选,
由用户/调用方确认后才挂链(与 OneEdit 的提议-验证范式一致)。
纯重命名(第一级)是哈希相等,确定性判断,才允许自动挂。

**端到端实测效果**(2026-09-01,容器内真实运行):

上传 `park-spec.md`(基础配送费 5 元)后,再上传改名+改价的
`park-spec-v2-renamed.md`(8 元,其余逐字相同):

```json
{
  "version_number": 1,
  "version_match": {
    "doc_id": "2361c2b1-...",
    "title": "park-spec.md",
    "similarity": 0.784,
    "exact_content": false
  }
}
```

命中第一候选、相似度 0.784(阈值之上)、未被自动挂链——按设计
返回待确认候选。确认后经版本化编辑挂链,即获得完整的改名+
修改版本链历史。

**已知边界**:
- 大幅重写(<0.70)的改名版会被判为独立文档——此时内容证据
  本身已不足,误挂比漏挂危害更大,宁可漏报;
- 对超短文档(shingle 集合太小)相似度区分度下降——测试数据显示
  重复串文本的 Jaccard 会被压低,建议文档长度下限约 50 字;
- 未来可用 embedding 全文向量替代字符 shingle(对语义改写更鲁棒,
  但需要 ollama 调用,当前方案零成本零依赖是刻意取舍)。

## 6. 验证记录

| 层 | 方式 | 结果 |
|---|---|---|
| 单元 | `uv run pytest`(Phase1: 22 个;Phase2 后 43 个,含 10 个改名识别) | 223 passed |
| 前端 | `npm test` + `npm run build` | 8 passed,构建通过 |
| 迁移 | 真实容器启动 | 4 列 + document_changes 表自动创建 |
| 端到端 P1 | 同名两版上传 / PUT 编辑 / diff API / 搜索 | 版本链正确翻转;7 条结构化 diff(added/removed/modified 齐全);搜索仅命中当前版;删除连带清理图谱 |
| 端到端 P2 | tkb_propose_edit 实调 | 定位受影响 chunk(rel 0.64)、提议只改目标处(5元→8元,加急费保留)、notes 3 条、next_step 引导落库 |
| 端到端 P2 | 改名+修改上传 | 相似度 0.784 正确返回候选,未自动挂链 |

## 7. 局限与后续(Phase 2+)

- ~~**意图路由**~~:已完成——agent 系统提示词 + `tkb-versions` skill
  教版本问答路由(list/diff/propose 工具选用)。
- ~~**横向修改传播**~~:提议管线已完成——`tkb_propose_edit` 定位受影响
  chunks、LLM 生成提议、跨文档一致性检查;落库走版本化
  `tkb_edit_document`。**待办:确认接口**(version_match 候选的手动挂链)。
- ~~**实体级边失效**~~:已完成——`get_full_graph`/`query_neighbors`/
  `get_entity_details` 过滤仅存于退版文档的实体。
- **改名识别的确认闭环**:`confirm_version_match` 接口(把疑似候选
  手动挂入版本链)尚未实现。
- **检索意图分类**的评测可参照 VersionQA 构建中文基准。
- **横向修改传播**:`tkb_apply_edit`(定位受影响 chunks -> 生成修改提议
  -> 跨文档一致性检查报告)未实现;OneEdit 的"提议-验证"范式是设计依据
- **实体级边失效**:图谱实体/关系尚未带 `valid_from_version` 标注
  (Graphiti `resolve_edge_contradictions` 的区间判定可参考)
- **检索意图分类**的评测可参照 VersionQA 构建中文基准

## 参考文献汇总

1. Huwiler, D., Stockinger, K., & Fürst, J. (2025). VersionRAG:
   Version-Aware Retrieval-Augmented Generation for Evolving Documents.
   arXiv:2510.08109. https://arxiv.org/abs/2510.08109
2. Rasmussen, P., et al. (2025). Zep: A Temporal Knowledge Graph
   Architecture for Agent Memory. https://www.getzep.com/
   代码:https://github.com/getzep/graphiti
3. Guo, Z., et al. (2024). LightRAG: Simple and Fast
   Retrieval-Augmented Generation. arXiv:2410.05779.
   https://github.com/HKUDS/LightRAG
4. Zhang, N., et al. (2024). OneEdit: A Neural-Symbolic Collaboratively
   Knowledge Editing System. (Semantic Scholar ID: 7ec21f9d85b9ecee22d0190f97a5b510f42901d1)
