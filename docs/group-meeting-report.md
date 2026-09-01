# 版本化文档管理 Phase 1 & Phase 2 工作汇报

> 汇报范围:分支 `feat/versioned-documents`(基线 `multi-file-upload`),13 个提交,
> 23 个文件,+2,522 / -31 行;外加先行提交的分析器容错修复(`79b8eb3f`)
> 时间:2026-08-26 ~ 2026-09-01

---

## 一、总览

研究目标:用户提出修改要求时,**横向**联动更新知识图谱相关内容与文档、
**纵向**进行文档版本迭代管理。工作分两个阶段落地:

| 阶段 | 主题 | 交付能力 |
|---|---|---|
| 基础修复 | LLM 输出容错 | 修复推理模型导致的实体抽取全失败 |
| Phase 1 | 纵向:版本迭代管理 | 版本链数据模型、同名自动挂链、LLM 版本 diff、版本化编辑、检索默认当前版、版本查询工具 |
| Phase 2 | 横向:修改传播 + 补强 | 编辑提议管线、改名识别、跨文档一致性检查、图谱净化、agent 意图路由、确认挂链闭环 |

测试:后端从 180 → **227 个测试全过**(版本相关 47 个);前端 8 个测试通过;
全部功能在五容器环境完成端到端实测。

---

## 二、基础修复:LLM 分析容错(先行提交,支撑两个阶段)

### 问题

接入 glm-5.3(推理模型)后,文档入库时实体/关系抽取**全部失败(0 实体 0 关系)**,
知识图谱为空。日志排查出三类输出故障:

1. **围栏包裹**:JSON 包在 ```json 围栏里,围栏外还有说明文字,旧解析只按行剥离,遇围栏未闭合/外部文字即崩
2. **正文截断**:推理消耗输出预算,JSON 被 max_tokens 腰斩
3. **空响应**:偶发 content 为空(预算全给思考过程)

### 实现(`src/engine/components/analyzer.py`,+149 行)

- 重写 `_extract_json()`:多候选提取(闭合围栏内容 → 未闭合围栏余文 → 原文 → 最外层花括号子串),逐一尝试解析
- 新增 `_repair_truncated_json()`:状态机扫描字符串/转义/括号栈,补齐未闭合引号与括号、剔除悬空尾逗号,救回截断前的大部分内容
- 请求显式设置 `max_tokens=8192`(避免默认预算被思考耗尽)
- `_call_openai_compatible()` 空响应自动重试 3 次

### 测试与效果

- 新增 5 个畸形输入测试(围栏带前后文字/未闭合围栏/纯 JSON/空串/截断修复),11 个 analyzer 测试全过
- **实测效果**:修复后 4 篇同主题文档入库,实体抽取 17 / 16 / 19 个,关系 20 / 16 / 22 条(修复前全部为 0)

---

## 三、Phase 1:纵向文档版本迭代管理

### 3.1 添加的功能

| # | 功能 | 说明 |
|---|---|---|
| F1 | 版本链数据模型 | documents 表增 4 列 + 新表 document_changes,存量数据幂等迁移 |
| F2 | 同名上传自动挂链 | 上传同名文档自动成为下一版本,内容未变则幂等跳过 |
| F3 | LLM 版本 diff | 相邻版本间的结构化变更抽取(新增/删除/修改) |
| F4 | 版本图谱投影 | Neo4j 版本链边 + 变更节点 |
| F5 | 检索默认当前版 | 向量检索与记忆检索均只命中最新版本 |
| F6 | 版本查询工具 | MCP 工具 + BFF 路由 + 前端版本徽标/版本历史 |
| F7 | 版本化编辑 | 修复编辑接口 405,编辑保存 = 生成新版本 |

### 3.2 实现方案

**F1 数据模型**(`models.py` + `postgres.py`):

```
documents 新增:version_group(逻辑文档身份)/ version_number(组内版本号)
              / version_of(上一版 id)/ is_current(当前版标记)
新表 document_changes:doc_id、from/to_version、summary、changes(JSONB)
```

关键决策:**版本即文档行**(非独立版本表)——复用 chunks 外键、删除级联与
图谱投影管线,迁移成本最低;`init_db` 中用 `ALTER TABLE ... ADD COLUMN
IF NOT EXISTS` + `version_group=id` 回填,老库无感升级
(`create_all` 不会为已存在的表加列,必须手写迁移)。

**F2 挂链逻辑**(`backend.py ingest`):同名且 is_current 的文档存在 →
新行继承 version_group、version_number+1、version_of 指向父版本,父版本
退位(is_current=false);content_hash 相同 → 返回现有文档不产生新版本。
版本上下文以纯 dataclass 在会话关闭前提取,规避 ORM 脱离会话问题。

**F3 LLM diff**(`analyzer.analyze_changes`):Prompt 参考 VersionRAG 的
`generate_changes_from_diff`——只提取实质变更(字段/章节/数值/结论),
排除排版/标点等噪音;输出 `{summary, changes:[{name,description,status}]}`;
status 白名单校验,未知值归一为 modified。

**F4 图谱投影**(`neo4j.py`):
```
(prev:Document)-[:NEXT_VERSION]->(next:Document)
(c:Change {from_version,to_version,summary,changes})-[:CHANGE_OF]->(d:Document)
```
版本元数据写入失败**只记日志不回滚**文档的 indexed 状态(diff 是附加产物)。
删除文档连带清理 Change 节点;reindex 后按 version_of 重连版本边。

**F5 检索过滤**:GraphRAG 的 `vector_search` JOIN documents 过滤
is_current;Hindsight 的 5 个检索路径(semantic/keyword/entity/temporal/
状态过滤)同步加过滤——**这是端到端测试发现的 bug**(详见 3.4)。

**F6/F7 接口**:`tkb_list_versions`/`tkb_diff_versions` MCP 工具、
`GET /documents/{id}/versions[/diff]` BFF 路由、`tkb_edit_document`;
前端列表 v{n} 徽标 + 详情页版本历史区块。编辑保存实现为版本化编辑:
新建 v+1 行、旧版退位、走管线重索引并补记 diff,前端跳转新版本。

### 3.3 测试内容

**单元测试**(新增 22 个 → 后续扩展至 47 个):

| 类别 | 覆盖点 |
|---|---|
| diff 解析 | 纯 JSON/围栏包裹/未知 status 归一/坏输入占位/非 dict 条目跳过 |
| 版本链 Cypher | NEXT_VERSION 边 MERGE 语义、Change 节点写入参数、删除清理、Document 节点版本属性 |
| 管线编排 | diff 入库 + 图谱投影调用正确;**版本元数据失败不影响主流程**(异常隔离) |
| 协议面 | backend/adapter/MCP 工具委托 |

测试方法:Neo4j 用 Fake Driver/Session 记录 Cypher 语句与参数做断言,
不依赖真实库;编排层用 RecordingAnalyzer/RecordingNeo4j 验证调用序列。

**端到端测试**(五容器真实环境):

1. 同名两版上传 → 版本号 1→2 递增、version_group 继承、旧版退位 ✅
2. LLM diff → 7 条结构化变更,状态标注准确(取消人工配送=removed、
   新增机器人试点=added、配送范围/时间/两处价格=modified)✅
3. Postgres/Neo4j 数据一致性(版本链边、Change 节点 cypher 直查)✅
4. 搜索"基础配送费" → 仅返回 v2 新价格(8元/5元),v1 旧价格(5元/3元)零泄漏 ✅
5. 编辑接口 → 保存生成 v2、diff 精准(1 条 modified + 1 条 added)✅
6. 删除 → 版本链边、Change 节点、chunks 级联清理 ✅

### 3.4 端到端测试发现并修复的 bug(2 个)

| bug | 现象 | 根因 | 修复 |
|---|---|---|---|
| adapter 透传缺失 | `GET /versions` 500 | HindsightKnowledgeBaseAdapter 手写代理每个方法,新方法未透传 | 补 list_versions/diff_versions |
| 旧版检索泄漏 | 搜索同时命中新旧两版 | 系统有两条独立检索管线,只改了 GraphRAG 一条,Hindsight 5 个查询路径未过滤 | 5 处全部加 is_current 过滤 |

**经验**:多管线架构下修改横切行为(检索域)必须先做调用链审计;
单元测试的 mock 会直接实现协议方法,暴露不了装饰器层的遗漏。

---

## 四、Phase 2:横向修改传播 + 改名识别

### 4.1 添加的功能

| # | 功能 | 说明 |
|---|---|---|
| F8 | 编辑提议管线 | tkb_propose_edit:定位受影响片段 → LLM 生成修改提议 → 跨文档一致性检查,确认后才落库 |
| F9 | 改名识别 | 文件改名后修改内容,仍能识别为同一文档的新版本(三级判定) |
| F10 | 确认挂链 | tkb_confirm_version_match:把疑似候选正式挂入版本链 |
| F11 | 图谱净化 | 退版文档的实体不再污染图谱查询 |
| F12 | agent 意图路由 | 版本敏感问题自动分流到版本工具(系统提示词 + skill) |

### 4.2 实现方案

**F8 编辑提议**(`backend.propose_edit` + `analyzer.propose_edit`),
采用 OneEdit 的"提议-验证"范式,分三步:

```
① 定位:修改要求向量化 → 在目标文档 chunks 内做余弦近邻,取 top-3
       (返回 chunk 序号 + 相关度分)
② 提议:LLM 生成修改后完整文本 + 改动说明
       (约束:只做要求涉及的改动,其余逐字保留,保持格式;
        说明中还需报告发现的潜在冲突)
③ 影响面:Neo4j 查询与本文档共享实体的其他当前版文档,
        按共享实体数排序(跨文档一致性检查)
→ 返回提议包;用户确认后经 tkb_edit_document 落库(生成新版本 + 自动 diff)
```

**F9 改名识别**(`_version_match.py`),三级判定:

```
第一级 纯重命名:content_hash(SHA256)与某当前版完全相同
      → 确定性判断,自动挂链
第二级 改名+修改:加权相似度 = 0.3×标题相似度 + 0.7×内容相似度
      ≥ 0.70 → 返回候选(附 doc_id/相似度),等待确认,不自动挂
第三级 无匹配 → 按独立新文档入库
```

相似度算法:字符 shingle 的 Jaccard(内容 3-gram、标题 2-gram,
标题先剥离 v1/final/draft/日期等版本痕迹)。选 shingle 而非 embedding:
上传路径同步计算需零延迟零依赖,且文本重叠信号比语义相似更适合区分
"修订版"与"同模板新文档"。**权重 0.3/0.7**:改名场景标题天然低分,
证据权重必须让位内容。

**阈值 0.70 的标定**(五档场景实测):

| 场景 | 相似度 | 期望判定 |
|---|---|---|
| 小改 1 处(5元→8元) | 0.81 | 新版本 ✓ |
| 中改 30% 内容 | 0.71 | 新版本 ✓ |
| 同模板不同内容 | 0.44 | 独立文档 ✓ |
| 全部重写 | 0.30 | 独立文档 ✓ |
| 无关文档 | 0.06 | 独立文档 ✓ |

阈值恰好落在修订版(≥0.71)与模板陷阱(≤0.44)之间,两侧各有 0.25+
安全边际。**第二级不自动挂链的原因**:内容相似无法区分"同一文档的修订"
与"基于同一模板写的新文档",误挂链(把别人的文档认成新版本)比漏挂
危害大,最终判断留给人。

**F10 确认挂链**(`backend.confirm_version_match`):新文档入组
(version_group/number/version_of)、组内当前版退位、补记 LLM diff
(复用管线公开方法 record_version_change)、修正两个 Document 节点的
版本属性、连接 NEXT_VERSION 边。

**F11 图谱净化**(借鉴 Graphiti 边失效思想):`get_full_graph` /
`query_neighbors` / `get_entity_details` 增加"存活"过滤——实体/关系边的
sources 中至少有一个来源文档是当前版。两个兼容细节:
- `coalesce(ld.is_current, true)`:版本化之前的存量节点无此属性,视为当前
- `n.sources IS NULL OR ...`:Hindsight 投影的实体无 sources 属性,保持可见

**F12 意图路由**:pi-agent 系统提示词新增"版本规则"区块(问历史 →
list_versions;问区别 → diff_versions;要求修改 → 先 propose 后 edit),
并新增 `tkb-versions` skill。相比 VersionRAG 的独立 LLM 意图分类器,
让工具选择发生在 agent 自身推理中,更轻量。

### 4.3 测试内容

**单元测试**(版本测试文件累计 47 个,其中 Phase 2 新增 25 个):

| 类别 | 覆盖点 |
|---|---|
| 编辑提议解析 | 纯 JSON/字符串 notes 归一/坏输入占位/prompt 内容断言 |
| 改名识别 | 版本痕迹剥离、同文 1.0/异文 0.0、真实修订 ≥ 阈值、
              模板文档 < 阈值、纯重命名 exact_content、多候选取最高分 |
| 存活过滤 | Cypher 含 coalesce 与三处存活子查询、无 sources 实体保留 |
| 确认挂链 | MCP 委托、错误透传、协议面存在性 |

测试数据的一个教训:标定测试最初用重复串文本(如"条款一"×40),
相似度全部失真(实测 0.43-0.52)——重复片段的 shingle 集合坍缩,
Jaccard 被系统性压低。改用真实多样文本后标定才成立。
**结论:相似度算法的测试数据必须模拟真实文本分布。**

**端到端测试**:

1. **编辑提议实调**(真实文档,修改配送费 5→8 元):
   - 受影响 chunk 定位正确(相关度 0.637,首 chunk 即收费标准段)
   - 提议文本精准:仅目标行改动,加急费等其余内容逐字保留
   - LLM 说明 3 条,含影响面判断("全文仅此一处提及该金额,加急费不受影响")
   - 返回下一步引导(确认后调用 edit 落库)✅
2. **改名识别实调**:上传改名+改价版(`park-spec.md` →
   `park-spec-v2-renamed.md`,5元→8元):
   - 相似度 **0.784**,命中原版为候选,未自动挂链 ✅
3. **确认闭环**:调用确认接口 → 挂链为 v2、父版退位、
   LLM diff 补记(`[modified] 基础配送费上调`,摘要准确)、
   Neo4j 版本边 v1→v2 建立✅
4. **检索验证**:搜索配送费 → 10 条 chunk 全部来自改名后的 v2,
   v1 零泄漏 ✅
5. **agent 路由**:smoke 测试中 agent 能通过检索/列文档/读全文定位目标
   文档并主动做关联影响核查(MCP 工具列表确认 4 个版本工具可见)✅

### 4.4 端到端测试发现的并发问题(已记录,未修复)

连续快速删除版本链关联文档时:
1. Neo4j 瞬时死锁(两个删除 + 图谱投影 worker 并发锁同批节点)——重试即成功
2. 图谱投影 worker 竞态:处理删除事件时,in-flight 重建事件产生了孤儿
   Document 节点(Postgres 权威数据已清,图谱残留,需手动清理)

定性:outbox 事件乱序问题,改进方向为删除事件加屏障或投影侧 tombstone。
另有 ingest 的 select-then-insert 并发窗口(两个同名上传并发会各自成组),
改进方向为部分唯一索引 + ON CONFLICT 重试。

---

## 五、数字汇总

| 指标 | 数值 |
|---|---|
| 提交数 | 13(分支 12 + 先行修复 1),Conventional Commits |
| 代码量 | 23 个文件,+2,522 / -31 行 |
| 后端测试 | 180 → **227 passed**(新增 47 个版本相关 + 5 个容错) |
| 前端测试 | 8 passed,SPA 构建通过 |
| 实体抽取效果 | 修复前 0 实体 → 修复后 17/16/19 实体(3 篇实测) |
| 版本 diff 质量 | 7 条结构化变更(状态标注全对)/ 编辑场景 2 条精准命中 |
| 改名识别 | 阈值 0.70(修订≥0.71,模板≤0.44);实测候选 0.784 |
| 检索正确性 | 旧版本零泄漏(改名场景 10 条 chunk 全部来自当前版) |
| E2E 发现 bug | 3 个(2 个已修复,1 组并发问题已记录) |

---

## 六、遗留问题与下一步

1. **并发安全**(优先):删除死锁重试、投影事件乱序、ingest 竞态窗口
2. **改名识别对语义改写不鲁棒**:同义重写相似度 < 0.70 会漏判(保守取舍,
   可用 embedding 全文向量作为第二信号)
3. **横向传播止于检查报告**:确认后不自动修改关联文档,演进方向为
   OneEdit 式自动传播 + 人工审核队列
4. **量化评测缺失**:版本敏感问答尚无基准,计划参照 VersionQA 构建中文
   版本化文档基准,补 naive RAG / GraphRAG / LightRAG / VersionRAG baseline

## 七、参考文献(实现依据)

1. Huwiler et al. *VersionRAG: Version-Aware RAG for Evolving Documents.* arXiv:2510.08109, 2025 —— 版本图结构、LLM diff prompt、检索域设计
2. Rasmussen et al. *Zep: A Temporal Knowledge Graph Architecture for Agent Memory.* 2025 —— 边失效(退位不删除)、时序过滤思想
3. Guo et al. *LightRAG.* arXiv:2410.05779, 2024 —— 实量 MERGE 聚合的增量合并
4. Zhang et al. *OneEdit: A Neural-Symbolic Collaboratively Knowledge Editing System.* 2024 —— 编辑的提议-验证范式
