# feat/versioned-documents 更新功能细则

> 分支:`feat/versioned-documents` → `main`
> 规模:19 个提交,34 个文件,+5,344 / -168 行
> 测试:后端 301 passed(新增 52 个版本相关)/ 前端 16 passed / pi-agent 36 passed
> 设计依据:VersionRAG (arXiv:2510.08109)、Graphiti/Zep、OneEdit、LightRAG

---

## 一、功能总览

| # | 功能 | 类型 | 涉及提交 |
|---|---|---|---|
| F1 | LLM 输出容错(围栏/截断/空响应) | 基础修复 | 79b8eb3f |
| F2 | 版本链数据模型与迁移 | feat | 40a93eec |
| F3 | 同名上传自动挂链 + LLM 版本 diff + 图谱投影 | feat | a2819592 |
| F4 | 检索默认当前版 + 版本查询工具(MCP/BFF/SPA) | feat | 76885661 |
| F5 | 版本化编辑(修复 PUT /content 405) | feat | 17fca214 |
| F6 | 编辑提议管线 + 改名识别 + 图谱净化 + 意图路由 | feat | ff8cb240 |
| F7 | 改名候选确认挂链 | feat | c1e5198b |
| F8 | 死锁与孤儿节点修复 | fix(合并内) | 65d54e6f |
| F9 | 嵌套依赖安全修复 | fix | 81ade48f |
| F10 | 双轮远端合并适配(架构重构兼容) | merge | ebf66a58 / 65d54e6f |
| — | 设计文档 / 组会报告 / 面试深挖文档 | docs | ee6f2e47 / ea50ef91 / cf1953d3 / 57f9cbee / 6349564f |

---

## 二、基础修复:F1 — LLM 输出容错

**问题**:接入推理型模型(glm-5.3)后,实体/关系抽取全部返回 0。

**改动**(`src/engine/components/analyzer.py`):

| 故障 | 表现 | 修复 |
|---|---|---|
| 围栏包裹 | JSON 套 ```json 围栏,围栏外有说明文字 | `_extract_json()` 多候选提取:闭合围栏内文 → 未闭合围栏余文 → 原文 → 最外层花括号子串,逐一尝试解析 |
| 正文截断 | 推理耗尽输出预算,JSON 被腰斩 | ① 请求显式 `max_tokens=8192`;② `_repair_truncated_json()` 状态机(扫描字符串/转义/括号栈)补齐未闭合引号与括号、剔除悬空尾逗号 |
| 空响应 | 偶发 content 为空 | `_call_openai_compatible()` 空响应自动重试 3 次 |

**效果**:修复前三篇文档 0 实体;修复后 17/16/19 实体。

---

## 三、纵向:版本迭代管理(F2–F5)

### F2 数据模型

```sql
-- documents 新增 4 列(幂等迁移:ALTER TABLE ... IF NOT EXISTS + 旧数据回填)
version_group UUID     -- 逻辑文档身份,同组 = 同一文档的各版本
version_number INT     -- 组内版本号,从 1 递增
version_of UUID        -- 上一版 doc_id(自引用 FK)
is_current BOOLEAN     -- 组内最新版标记(检索域)

-- 新表
document_changes(id, doc_id FK CASCADE, from_version, to_version,
                 summary TEXT, changes JSONB)
-- changes: [{name, description, status: added|removed|modified}]
```

设计决策:
- **版本即文档行**(非独立版本表):复用 chunks 外键、删除级联、图谱投影管线,迁移成本最低
- **退位不删除**(Graphiti 边失效思想):旧版全文/chunks/图谱全部保留,支撑追溯与未来回滚
- `create_all` 不为已存在表加列 → `init_db` 手写幂等迁移

### F3 同名上传自动挂链

`backend._ingest_one`:同名且 is_current 的文档存在 → 新行继承
version_group、version_number+1、version_of 指向父版,父版退位;
content_hash 相同 → 幂等返回现有文档。版本上下文(VersionParent
纯 dataclass)在会话关闭前提取,避免 ORM 脱离会话。

**LLM 版本 diff**(`analyzer.analyze_changes`):Prompt 参考 VersionRAG
`generate_changes_from_diff`——只提取实质变更(数值/结论/章节/定义的
增删改),排除排版/标点/空白噪音;status 白名单校验。

**图谱投影**(`neo4j.py`):
```
(prev:Document)-[:NEXT_VERSION]->(next:Document)
(c:Change {from_version,to_version,summary,changes})-[:CHANGE_OF]->(d)
```
版本元数据失败只记日志不回滚文档 indexed 状态(附加产物不拖垮主流程)。

### F4 检索默认当前版

| 管线 | 改动 |
|---|---|
| GraphRAG `_search.vector_search` | JOIN documents 过滤 `is_current = true`(新增 `current_only` 参数) |
| Hindsight `repository.py` | 5 个检索路径(semantic/keyword/entity/temporal)+ backfill 清单全部加过滤 |
| 图谱 `get_full_graph`/`query_neighbors`/`get_entity_details` | "存活"过滤:实体/关系边 sources 至少有一个当前版来源;`coalesce(is_current, true)` 兼容存量节点;`sources IS NULL` 保留 Hindsight 实体 |

**对外接口**(MCP + BFF + SPA):
- `tkb_list_versions` / `GET /documents/{id}/versions` — 版本链 + 每版摘要
- `tkb_diff_versions` / `GET /documents/{id}/versions/diff` — 结构化对比
- SPA:列表 v{n} 徽标(历史版置灰),详情页版本历史时间线

### F5 版本化编辑

前端 `PUT /documents/{id}/content` 原为 405(接口从未实现)。现实现为:
`edit_content`(保留远端的 is_public_document 可见性检查)→ 委托
`edit_document`(版本化:新建 v+1 行、旧版退位、重索引 + 自动 diff),
前端保存后跳转新版本详情页。

---

## 四、横向:修改传播(F6–F7)

### F6a 编辑提议管线(tkb_propose_edit,OneEdit 提议-验证范式)

```
① 定位:edit_request 向量化 → 该文档 chunks 余弦 top-3(带 relevance)
② 提议:LLM 生成修改后全文 + notes(prompt 约束:只做要求涉及的改动,
   其余逐字保留;notes 须报告潜在冲突)
③ 影响面:Neo4j find_related_docs_via_entities —— 与本文档共享实体的
   其他当前版文档,按共享数排序(跨文档一致性检查)
→ 返回提议包;用户确认后 tkb_edit_document 落库(自动新版本 + diff)
```

实测(改配送费 5→8 元):定位相关度 0.637 命中首 chunk;提议只改目标行;
notes 主动指出"全文仅此一处提及该金额,加急费不受影响"。

### F6b 改名识别(_version_match.py,三级判定)

```
第一级 纯重命名:content_hash 与某当前版完全相同 → 自动挂链(确定性)
第二级 改名+修改:0.3×标题相似度 + 0.7×内容相似度 ≥ 0.70
        → 返回 version_match 候选,等待确认,不自动挂
第三级 无匹配 → 独立新文档
```

- 相似度 = 字符 shingle 的 Jaccard(内容 3-gram、标题 2-gram,
  标题先剥离 v1/final/draft/日期等版本痕迹)
- 阈值标定:小改 0.81 / 中改 0.71 / **同模板不同文 0.44** / 重写 0.30
  / 无关 0.06 —— 0.70 恰在修订版与模板陷阱之间,两侧 0.25+ 边际
- 选 shingle 而非 embedding:同步路径零延迟、确定性可审计、
  "文本重叠"信号比"语义相似"更适合区分模板陷阱
- 第二级不自动挂:内容相似无法区分修订与模板新文,误挂比漏挂危害大

### F6c agent 意图路由

pi-agent 系统提示词新增"版本规则"区块 + `tkb-versions` skill:
问历史 → list_versions;问区别 → diff_versions;要求修改 → 先提议后落库。

### F7 确认挂链(tkb_confirm_version_match)

新文档入组(version_group/number/version_of)、组内当前版退位、
补记 LLM diff(复用 `pipeline.record_version_change` 公开方法)、
修正两个 Document 节点版本属性、连接 NEXT_VERSION 边。
已完成命令的幂等返回(already_linked)。

---

## 五、并发修复:F8(端到端发现,合并在 65d54e6f 内)

**死锁根因**:`delete_document_graph` 逐条 SET 更新实体 sources;
版本链两版共享同一批 MERGE 实体,两个并发删除交叉加锁 →
Neo4j TransientError.DeadlockDetected → 接口 500。

**孤儿根因**:`remove()` 先删 Postgres(已提交)再清图谱;死锁时图谱
清理半途而废;重试时 `if not doc: return` 把清理挡在门外 →
Document 节点永久残留。

**三层防线**:
1. sources 清理合并为**单条 UNWIND 批量写**(一个事务一次性加锁)
2. 读取 **ORDER BY name** 确定加锁顺序,并发删除顺序一致
3. **瞬态错误指数退避重试**(3 次,基数 0.5s)
4. `remove()` **无条件执行图谱清理**(幂等,Postgres 行缺失也清理)

**实测**:并发删除版本链两版 → 双 200;日志显示重试吸收了一次死锁
(第 1 次重试成功);图谱 Document/Change 节点零残留。

---

## 六、依赖安全修复:F9

合并带入的依赖树存在新披露漏洞,导致 pi-agent 镜像构建的
`npm audit --audit-level=high` 门禁失败:

| 依赖 | 漏洞版本 | 修复 |
|---|---|---|
| fast-uri | 3.0.0–3.1.5(GHSA-5jgf-p345-68v8) | override → 3.1.7 |
| undici(嵌套于 pi-coding-agent) | 8.0.0–8.8.0 | override → 8.9.0 |
| brace-expansion(嵌套) | 4.0.0–5.0.8 | override → 5.0.9 |

处理:package.json 加 `overrides` + 删除 lockfile 陈旧嵌套条目强制重解析
→ `npm audit` 0 漏洞,`npm run security` 门禁通过,镜像构建恢复。

---

## 七、远端合并适配:F10(两轮)

远端在分支开发期间两次大幅演进,共合并 50 个提交:

**第一轮**(ebf66a58):会话记忆系统、文档可见性
(is_public_document)、重试路由、批量上传、相关性阈值。

**第二轮**(65d54e6f):架构级重构——
- `engine/mcp.py` → `agent/tkb/mcp/server.py`(git 重命名检测自动把
  我们的 tkb_* 工具带到新位置)
- 删除 `agent/engine_client.py` 与 Hindsight adapter,BFF 直连 KnowledgeBase
- Pipeline 重构:信号量并发 + 进度追踪 + 方法拆分
- settings 分组化(llm/embedding 嵌套结构)

适配方式:以远端为基底外科手术式重植版本功能(版本化 _ingest_one、
双管线 is_current 过滤、版本钩子按条件传参、测试迁移到新 settings API
与新 MCP 模块路径),`edit_content` 保留可见性检查后委托版本化实现。

---

## 八、端到端验证记录

| 场景 | 结果 |
|---|---|
| 同名两版上传 | 版本号递增、组继承、旧版退位 ✓ |
| LLM diff | 7 条结构化变更,三种状态标注全对 ✓ |
| 版本化编辑 | 生成 v2,diff 精准(1 modified + 1 added)✓ |
| 编辑提议 | 定位/提议/影响面三步输出正确 ✓ |
| 改名识别 | 相似度 0.935 返回候选,未自动挂链 ✓ |
| 确认挂链 | v1→v2 边建立、diff 补记、父版退位 ✓ |
| 检索当前版 | 旧版内容零泄漏(10 chunks 全来自新版)✓ |
| **并发双删(死锁场景)** | **双 200;重试吸收 1 次死锁;图谱零残留** ✓ |
| 删除级联 | 版本边/Change/chunks/上传目录全清理 ✓ |

## 九、已知局限(主动交底)

1. ingest 的 select-then-insert 并发窗口(方案:部分唯一索引 + ON CONFLICT 重试)
2. 改名识别不识别同义改写("配送费"→"运输费"相似度跌破阈值;方案:embedding 第二信号)
3. 横向传播止于检查报告,不自动修改关联文档(方案:自动传播 + 审核队列)
4. 意图路由为提示词级,未做量化评测(方案:参照 VersionQA 建中文基准)

## 十、文档产出

| 文件 | 用途 |
|---|---|
| docs/versioned-documents-design.md | 设计文档:论文依据、前后对比、改名识别机制与阈值标定 |
| docs/group-meeting-report.md | 组会汇报:功能/实现/测试/效果 |
| docs/interview-deep-dive.md | 面试深挖:模块细节、踩坑记录、Q&A 预案 |
