# AI 自动文件归档 —— 设计规划与实现流程

> 状态: V2 设计修订中（对应 OpenSpec change: `add-auto-file-archiving`）
> 初版日期: 2026-09-08；V2 修订: 2026-09-10
> 前置调研: [AI_Auto_File_Archiving_Source_Level_Research.html](./AI_Auto_File_Archiving_Source_Level_Research.html)

---

## 1. 背景与动机

### 1.1 现状问题

对当前知识库（team-knowledge-base）的代码级盘点揭示了三个事实：

1. **系统是 text-first 的**。上传文件被提取出 `raw_text` 存入 Postgres 后，
   检索、问答、图谱全部只消费文本。原始文件本体只是附带品，SPA 没有
   "下载原文件"能力。
2. **原始文件在 LAN 部署中是易失的**。`UPLOAD_DIR = Path("uploads")` 落在
   容器可写层，compose 未挂卷；CI 管线每次合并 main 重建容器，文件字节
   即丢失，`documents.file_path` 变成悬空指针（`reingest` 对老文档失效）。
3. **知识库文档没有任何目录/分类结构**，团队没有文件整理与保管的完整能力。

### 1.2 目标（路线 C：物理归档 + 知识库联动）

在现有引擎上新增**自动文件归档**能力：

- 持久 workspace（收件箱 + 归档目录树），原始文件可保管、不随部署丢失；
- 文件进入收件箱后由 AI 分类，**高置信自动归档、低置信由用户确认**；
- 文件只提取一次，归档决策与 GraphRAG 知识分析消费同一份 FileContext
  并行运行；
- 所有文件移动可撤销（操作日志 + 反向执行）；
- 对已经入库但尚未归档的存量文档，提供 Sortio 风格的扫描、预览、选择和
  批量归档；默认归档规则可由用户修改。

### 1.3 调研结论的落地取舍

源码级调研（Forel / Hazelnut / Paperless-ngx / Sortio / GenAI Organizer）
给出的通用架构是:

```
Watcher -> 稳定性检查 -> 持久队列 -> 内容提取 -> 规则/检索+LLM 决策
-> 行动计划 -> 安全校验 -> 执行 -> 操作日志 -> 撤销
```

本设计的核心原则直接继承自它：

> **LLM 不能直接操作文件系统，只能产生结构化决策。**

同时按本项目实际做了裁剪:

| 调研建议 | 本项目取舍 | 理由 |
|---|---|---|
| Watcher 用 FSEvents/inotify | **轮询 + stat 稳定性检查** | BFF 进程内 asyncio worker 即可，无需新依赖；5-10s 轮询对归档场景足够实时 |
| Rules First, AI 兜底 | **用户 policy + 检索 + LLM** | 默认项目优先 policy 可编辑；确定性规则先匹配，未命中再用目录画像和 LLM |
| Celery + Redis 任务队列 | **Postgres 单表队列** | 仓库已有被两个 worker（graph_outbox / conversation_worker）验证过的 claim/lease/retry 模式，不新增运维面 |
| 每个文件都过 LLM-as-Judge | **确定性 Validator** | 调研自己也建议: 优先确定性校验，低置信/高风险才升级 |
| Dry Run 预览 | **二态分流 + 全量审核开关** | 低置信样本给推荐并等待确认，高置信样本直接执行；存量文件统一先预览 |

---

## 2. 现有代码复用清单

| 归档子系统 | 复用的现有资产 | 位置 |
|---|---|---|
| 内容提取 (FileContext) | 多格式提取器注册表 (pdf/docx/pptx/md/image-OCR) | `src/engine/components/extractors/registry.py` |
| LLM 结构化决策 | prompt 构造 → `_extract_json` 容错解析（围栏剥离、截断修复）→ dataclass 的完整范式 | `src/engine/components/analyzer.py` |
| 候选目录检索 | embedding 客户端 + pgvector | `src/engine/components/embedder.py` |
| 候选语料 | 已入库文档的 `overview`（目录画像的原料） | Postgres `documents` 表 |
| 持久任务队列 | `FOR UPDATE SKIP LOCKED` + lease + attempts + 指数退避的队列模式 | `src/engine/hindsight_components/graph_outbox.py` |
| 后台 worker 生命周期 | BFF lifespan 内启停的 asyncio 任务封装 | `hindsight_components/graph_runtime.py` + `webapp/server/deps.py` |
| 入库联动 | `IngestSource` 已支持 `path=`；整条 ingest 流水线 | `src/engine/interface.py`, `graphrag/backend.py` |
| 人审 UI 骨架 | 页面/路由/api-client/轮询模式 | `webapp/client/src/` |

**关键联动收益**: 归档后的文件以 `IngestSource(path=归档路径)` 入库，
`documents.file_path` 直接指向 workspace 内的归档位置——归档目录成为
知识库的持久文件仓，两个系统共享同一份原件，不需要二次拷贝。

### 2.1 用通俗语言理解：原系统已经会“读懂文件”，新系统增加“整理文件”

可以把整个应用看成一家已有“知识加工厂”、现在新增“智能收发室”的公司：

- **原有知识库流水线是知识加工厂**：收到一个确定要入库的文件后，提取
  全文、切成 chunks，对整篇文档生成 overview，对每个 chunk 调用 LLM
  抽取实体和关系，同时生成 chunk embedding，最后写入 Postgres 和 Neo4j，
  供搜索、问答和 GraphRAG 使用。
- **新增归档流水线是智能收发室**：先保管原文件，判断它应放进哪个物理
  目录、是否要改名、能否自动执行；归档完成后再把归档路径交给原有知识
  加工厂入库。

所以本变更不是用“整文件分类”替换原来的“chunk 分析”，而是在原有入库
流水线之前增加一个文件治理阶段。二者解决的问题不同：

| 对比项 | 新增归档流程 | 原有知识库入库流程 |
|---|---|---|
| 要回答的问题 | 文件放在哪个目录、叫什么名字 | 文件讲了什么、包含哪些实体和关系 |
| LLM 输入粒度 | 文件提取文本的前 3000 字符 + Top-K 目录画像 | 整篇原文生成 overview；每个 chunk 分别抽取实体/关系 |
| LLM 输出 | `candidate_id`、置信度、新文件名和理由 | overview、实体、关系、文件关系 |
| 是否移动文件 | 是，但由 Validator/Executor 执行 | 否，只建立知识索引和图谱 |
| 主要落库 | `archive_jobs`、`archive_operations` | `documents`、`chunks`、Neo4j 图谱 |

### 2.2 两条流程如何分开、如何连接、如何复用

两套业务能力仍然分工明确，但 V2 在执行上改成**共享一次提取、并行分析、
分别提交结果**。二者不是前后等待，也不是把 chunk 抽取结果拿去做归档：

```
新增归档流水线
inbox 文件
  -> 稳定性检查
  -> extract 一次，生成不可变 FileContext(raw_text/hash/metadata)
                  |
          +-------+-----------------------------------+
          |                                           |
          v                                           v
  归档决策分支                                 原有知识分析分支
  前 3000 字符 + 目录画像                      FileContext.raw_text
  -> 候选目录向量检索                          -> chunk_text
  -> LLM 整文件分类                            -> overview/file_relations
  -> Planner / Validator                       -> chunk entities/relations
  -> high: move + journal                      -> chunk embedding
     low: awaiting_review                      -> documents/chunks + Neo4j
          |                                           |
          +----------------+--------------------------+
                           v
                    协调提交最终状态
              documents.file_path 指向 inbox 或 archive
                    文件可检索、可问答
```

边界和复用点如下：

1. **流程边界清楚**：`archive.worker` 负责接收、分类、规划和移动；
   `graphrag.pipeline` 负责知识分析。协调器只传递不可变 `FileContext`、
   `kb_doc_id` 和最终文件路径，不让两个分支互相调用内部实现。
2. **只提取一次**：协调器先调用 `extractors.registry` 生成 FileContext；
   两个分支都消费其中的 `raw_text`。知识管线不得再从路径重复读取文件，
   从而避免分析期间文件被 move 的竞态。
3. **复用 LLM 基础能力**：归档分类器复用 `Analyzer` 的模型调用和
   `_extract_json` 容错解析，但使用独立的分类 prompt 和独立输出契约；
   它不复用 chunk 实体抽取的结果。
4. **复用 embedding 基础设施，但向量用途不同**：归档阶段用文件摘要
   embedding 匹配“目录画像”；入库阶段为每个 chunk 生成 embedding，供
   文档检索与问答使用。
5. **复用已有目录知识**：目录画像会聚合该目录下已入库文档的
   `documents.overview`。这些 overview 是原有入库流程生成的，所以知识库
   的既有处理结果反过来帮助下一批文件分类。
6. **共享一份正式原件**：高置信自动归档后，`documents.file_path` 指向
   `workspace/archive/...`；低置信待审时先指向 `workspace/inbox/...`，批准
   move 后只更新路径，不重新入库。无论哪种状态都只有一份原文件。

实现上需要给 `IngestSource` 增加可信的 `extracted_text`（或引入等价的
`FileContext`/`PreparedIngest` 契约），让 `graphrag.pipeline` 可以跳过二次
extract。当前代码仍是“归档先执行、ingest 再提取”的串行实现，须按本节
重构后才达到 V2 并行语义。

---

## 3. 总体架构（V2）

V2 的主干以 2.2 节“单次提取、双分支并行”为准。下面的大图保留扫描、
队列、校验、执行和日志等组件关系，但其中“归档完成后才触发 ingest”的
串行箭头属于 V1；实现时应由协调器在 FileContext 产生后同时启动归档决策
和知识分析，并在 move 后更新 `documents.file_path`，不得再次 ingest。

```
宿主机 / LAN                       webapp 容器 (单 BFF 进程)
-----------                        ------------------------------------------
workspace 卷 (持久)                +--------------------------------------+
+-- inbox/      <=== 文件入口 1: Web 上传 (POST /api/archive/inbox)
|                <=== 文件入口 2: 宿主机直接投放 (scp/SMB)
+-- archive/                       |  扫描器 worker (轮询 inbox)           |
|   +-- 财务/发票/                  |   - 临时文件过滤 (.part/.crdownload)  |
|   +-- 项目/XXX研究/               |   - size+mtime 连续 N 次稳定          |
|   +-- 会议纪要/                   |   - sha256 去重                       |
+-- uploads/    (原 uploads 迁入)   |   - discovered -> queued 入队          |
                                   +------------------+-------------------+
                                                      v
                                   +--------------------------------------+
                                   |  archive_jobs 表 (Postgres 持久队列)   |
                                   |  claim/lease/attempts/退避, 重启续跑    |
                                   +------------------+-------------------+
                                                      v (归档 worker)
                                   +--------------------------------------+
                                   |  registry.extract() -> FileContext    |
                                   |  (只提取一次，供两个分析分支共享)         |
                                   +------------------+-------------------+
                                          |                           |
                                          v                           v
                          +--------------------------+  +-------------------------+
                          | 目录画像 + 候选检索        |  | LLM 分类器               |
                          | (overview 聚合 + embedder |->| candidate_id + confidence|
                          |  Top-K)                   |  | + new_name + rationale   |
                          +--------------------------+  +------------+------------+
                                                                      v
                                                        +----------------------------+
                                                        | Planner + Validator        |
                                                        | 边界/穿越/冲突/指纹复核      |
                                                        +------------+---------------+
                                                                      v
                                                            +---------+---------+
                                                            |     二态置信度分流  |
                                                            +---------+---------+
                                     +------------------------+---------------------+
                                     v (>= threshold)         v (< threshold)
                          +-----------------------+  +-----------------------------+
                          | 自动执行               |  | awaiting_review             |
                          | 已有/新目录均可         |  | 确认 / 改分类 / 暂不归档      |
                          | move -> journal       |  | 文件尚未 move，仍在 inbox     |
                          +-----------+-----------+  +---------------+-------------+
                                      |                              | 确认/改分类
                                      +---------------+--------------+
                                                      v
                                           ArchiveOperation + 更新
                                           documents.file_path
                                                      |
                                                      v
                                           [撤销] 反向 move + 路径回写
```

**核心性质**: 自动路径与人审批准走完全相同的执行代码。人审不是另一套
逻辑，只是决策门的另一种出口——"全量审核"测试开关因此天然成立。

---

## 4. 关键设计决策

### 4.1 模块划分

归档作为引擎能力，放在 `src/engine/components/archive/`（与 extractors、
embedder 同级）。BFF 只做路由与依赖注入；不放 `src/agent/`（无状态技能层），
也不新建顶层模块。

```
src/engine/components/archive/
├── scanner.py      # 轮询 inbox: 稳定性 + 过滤 + 去重
├── jobs.py         # ArchiveJob 模型 + 队列 (仿 graph_outbox)
├── worker.py       # job -> 分类 -> 分流 -> 执行/入待审
├── classifier.py   # 目录画像 + 候选检索 + LLM 结构化决策
├── planner.py      # ActionPlan 构造 + 安全校验
├── executor.py     # move + journal + 触发 kb.ingest
├── journal.py      # ArchiveOperation + undo
└── runtime.py      # scanner+worker 生命周期封装 (仿 graph_runtime)
```

### 4.2 数据模型（V2）

**`archive_jobs`** —— 持久队列:

| 字段 | 说明 |
|---|---|
| id / file_path / content_hash | 文件标识（hash 去重键） |
| status | discovered → queued → processing → awaiting_review / executing → done；另有 unarchived / failed / dead |
| attempts / locked_at / available_at | claim/lease/退避（同 graph_outbox 语义） |
| plan (JSONB) | LLM 决策 + 校验结果（待审时供前端展示证据） |
| kb_doc_id | 知识分析分支创建的文档；归档后只更新其 file_path，不重复 ingest |
| error_msg / routing_reason | 失败信息 / 进待审的原因 |

**`archive_operations`** —— 操作日志（撤销的依据）:

| 字段 | 说明 |
|---|---|
| source_path / destination_path | 可逆执行的两端 |
| content_hash | 撤销前的一致性指纹 |
| decision_source | auto / review / manual |
| confidence / rationale | 决策元数据（历史台账展示） |
| kb_doc_id | 新文件并行知识分析产生的文档，或存量文档原有 ID |
| status / undo_status | executed / undone；undo 冲突记录 |

**`archive_policies`** —— 用户可编辑的归档规则：

| 字段 | 说明 |
|---|---|
| enabled / name / priority | 是否启用、规则名称、执行顺序 |
| instructions | 给分类 Agent 的业务规则，例如“优先按项目归档” |
| match / destination_template | 可选的确定性匹配条件与目标模板 |
| allow_new_directory | 是否允许高置信决策自动创建新目录 |

**`archive_migration_batches`** —— 存量归档批次：记录扫描范围、规则版本、
dry-run 预览、用户选择、执行进度与失败项，保证批量操作可追踪、可恢复。

### 4.3 二态置信度分流（V2 用户决策）

```
                 confidence
  0 ────────────────────────┬────────────────────────── 1
                         threshold
       低置信，等待用户确认  |   高置信，自动执行
                             |
  awaiting_review            |   auto
  推荐已有目录或新目录         |   已有目录或新目录均可
  [确认] [改分类] [暂不归档]   |   Planner/Validator 通过后 move
```

- `threshold` 默认 0.75，可由用户配置；V2 删除 `delta` 和第三种低置信
  `skipped` 分支。
- 高置信决策可以选择已有目录，也可以新建目录；是否允许自动新建由当前
  `archive_policy.allow_new_directory` 控制，且必须通过 Validator。
- 低置信决策也必须给出完整推荐：推荐已有目录，或明确建议一个新目录，
  供用户确认或修改。只有模型调用本身失败时才显示可重试错误，且该项不可执行。
- “暂不归档”把 job 标记为 `unarchived`，文件一直留在 inbox。它此前从未
  move，因此不是“拒绝后返回 inbox”；以后可重新规划或手动归档。
- `review_all` 开关: 开启后所有决策（无论置信度）都进待审，用于上线前
  观察 AI 判断质量。
- LLM 禁用或输出不可解析 → job failed，**绝不默认执行**。

### 4.4 LLM 决策契约

LLM 不能自由生成绝对路径，但每次都必须对称比较以下两类方案：

1. 复用本次 Top-K 中的某个已有语义目录；
2. 创建一个新的项目或主题目录。

目录树不为空时仍允许新建目录，目录树为空时则由第一批文件冷启动出有语义
的目录。`待整理`、`待确认`、`未分类` 等操作性兜底目录可以保留在目录树和
历史中，但不进入 Top-K 语义检索，避免所有后续文件被同一个兜底目录吸附。
`candidate_id` 与 `new_subdirectory` 必须且只能填写一个：

```json
{
  "candidate_id": "archive/财务/发票",
  "new_subdirectory": null,
  "confidence": 0.91,
  "new_name": "2026-09-ACME-发票.pdf",
  "rationale": "内容为一张 9 月发票, 涉及 ACME 公司"
}
```

- prompt 中编入 Top-K 候选目录及其画像摘要（该目录已归档文档 overview
  的聚合 + 目录名），以及文件摘要（FileContext 提取文本的压缩）。
- 已有候选只是“复用”方案的检索结果，不拥有默认优先权。模型理由必须说明
  为什么复用已有目录，或为什么文件形成了值得新建的独立项目/主题。
- `new_subdirectory` 可表达规则允许的项目层级，例如 `项目/Project-A`；
  最终层级、名称和根目录仍由后端 policy + Validator 限制。
- 解析复用 `_extract_json` 同款容错（围栏剥离、截断修复）。
- 后端把 candidate_id 解析为真实路径并做全部校验——LLM 输出永远不是
  路径的最终权威。

### 4.5 安全校验（执行前，确定性代码）

1. **工作区边界**: 解析后的目标必须在 `archive/` 之内。
2. **路径穿越**: `../` 归一化后复核（复用 `_safe_filename` 思路）。
3. **目标冲突**: 同名文件存在时按配置走"确定性后缀"或拒绝，绝不覆盖。
4. **源指纹**: 执行前源文件 sha256 必须仍等于分类时的 hash。
5. **权限**: 目标目录可写。
6. **Loop 防护**: executor 只往 archive/ 移动，scanner 只扫 inbox/，
  移动产生的事件天然不在扫描范围。

### 4.6 撤销语义

```
undo(operation):
    校验 destination 当前 hash == operation.content_hash
      ├── 匹配   -> 反向 move(destination -> source)
      │            -> 更新 documents.file_path 为原 inbox/legacy 路径
      │            -> operation.undo_status = undone
      └── 不匹配 -> 拒绝, 报告"归档后文件已被修改"
```

V2 中知识分析与归档并行，undo 只撤销文件组织结果，不删除已经形成的知识。
文件回到 inbox 后仍可检索；若用户明确选择“同时从知识库删除”，再调用
`kb.remove`，两种动作必须在 UI 上区分。

### 4.7 前端（人审面）

单页五 Tab + 模式开关，路由 `/archive`，导航入口"归档":

```
| 归档                                              [模式: 自动 v] |
|  [待确认 (2)] [暂未归档] [存量归档] [规则] [历史/目录树]               |
|  (低于阈值或全量审核模式的文件出现在这里)                              |
|  +------------------------------------------------------------+  |
|  | report.pdf     置信度 0.42   原因: 置信度低于自动阈值           |  |
|  | 摘要: ...   相似已归档: a.md, b.md                            |  |
|  | 建议: archive/项目/归档测试/ (新建目录)                        |  |
|  |        [确认归档]  [暂不归档]  [改分类 v]  [查看原文]           |  |
|  +------------------------------------------------------------+  |
|  历史: 时间 · 文件 · 源->目标 · 置信度 · 来源(自动/人审/手动) · [撤销] |
|  目录树: 只读浏览, 每目录显示画像与已归档文档数                      |
```

- 待审列表 10s 轮询（与现有 pipeline progress 轮询同模式），不引入
  SSE/WebSocket。
- 现有"上传文件"按钮改走 `/api/archive/inbox`；原 `/api/documents/upload`
  直接入库接口保留兼容。
- “存量归档”默认关闭；开启后先扫描和生成 dry-run 预览，用户选择全部或部分
  文档并确认后才批量执行，不因打开页面自动移动历史文件。

### 4.8 API 面（`/api/archive/*`）

| 端点 | 说明 |
|---|---|
| `POST /inbox` | Web 上传 → inbox（归档流水线入口） |
| `GET /reviews` | 待审列表（含决策证据） |
| `POST /reviews/{job_id}/approve` `.../defer` `.../reassign` | 确认、暂不归档、改分类 |
| `GET /unarchived` + `POST /unarchived/{job_id}/replan` | 暂未归档列表 + 重新规划 |
| `GET /operations` | 历史台账 |
| `POST /operations/{id}/undo` | 撤销 |
| `GET /mode` `PUT /mode` | 自动 / 全量审核切换 |
| `GET /tree` | 目录树 + 画像 |
| `GET/PUT /policies` | 查看和修改默认归档规则 |
| `POST /legacy/scan` | 扫描已有但未归档的文档，返回可归档/原件缺失清单 |
| `POST /legacy/plan` | 按当前规则生成批量 dry-run 预览 |
| `POST /legacy/execute` | 对用户选中的预览项执行批量归档 |

所有变更端点走与流水线相同的校验、执行、journal 代码。

### 4.9 配置与部署

`config/app.yaml`:

```yaml
archive:
  enabled: true
  workspace_dir: workspace        # 容器内 /app/workspace
  threshold: 0.75
  review_all: false
  poll_seconds: 5
  stability_checks: 2             # 连续 N 次 stat 不变
  max_attempts: 5
  new_subdirectory_roots: [archive]
  legacy_archiving_enabled: false
  default_policy: project_first
```

compose: webapp 增加 `workspacedata:/app/workspace` 卷; inbox、archive、
uploads（原 `UPLOAD_DIR` 改指卷内）都在其下。存量容器层 uploads 不迁移。

### 4.10 默认规则与用户修改

默认 policy 为“项目优先”：如果文件内容能够识别出明确项目，优先归档到
`archive/项目/<项目名>/`；不能识别项目时，再按现有目录画像选择最相近
目录。policy 作为版本化数据保存在 Postgres，而不是只写死在 prompt：

```json
{
  "name": "项目优先",
  "instructions": "优先识别文档所属项目；同一项目放入项目同名目录。",
  "allow_new_directory": true,
  "fallback": "semantic_folder_profile"
}
```

用户修改规则后，只影响新 job 和主动点击“重新规划”的未归档 job；已经执行
的历史操作不自动搬迁。每个 plan 记录 `policy_id` 和 `policy_version`，以便
解释某个文件当时为什么被这样归档。

### 4.11 存量文档归档（Sortio 风格）

存量归档只处理满足以下条件的公开文档：当前版本、`file_path` 不在
workspace/archive 内、未被已有 ArchiveOperation 管理、且原文件仍存在。

```
用户开启“存量归档”
  -> 扫描 documents 与物理文件
  -> 分成 可归档 / 已归档 / 原件缺失 / 路径冲突
  -> 按当前 policy 批量生成计划（dry run，不移动）
  -> UI 展示 原路径 -> 建议路径、置信度、是否新建目录
  -> 用户全选、部分选择、改目录或取消
  -> 确认后批量 move + journal
  -> 更新原 Document.file_path，保留 doc_id/chunks/Neo4j，不重复入库
```

批量执行逐文件提交，不采用“全批次一个事务”：一个文件失败不回滚已经成功
的文件，但批次记录每项结果并允许重试。撤销也逐条使用同一个 journal 语义。
原件缺失的历史文档只报告问题，不能根据 `raw_text` 悄悄伪造原始 PDF/DOCX；
Markdown/TXT 可另行提供“从 raw_text 重建文本文件”的显式操作。

---

## 5. 实现流程（V2）

完整可执行任务见 `openspec/changes/add-auto-file-archiving/tasks.md`。实施顺序：

1. 先引入 `FileContext`/`extracted_text`，让现有 GraphRAG pipeline 能消费
   已提取全文并跳过二次 extract。
2. 将 worker 改为归档分类与知识分析并行，补齐 `kb_doc_id` 和最终路径协调。
3. 将原三段置信度带迁移为二态阈值，完成 auto/review/defer/replan 状态机。
4. 修改 executor/undo：移动后更新已有 Document 路径，不重复 ingest；
   默认撤销只反向 move，不删除知识。
5. 增加版本化 policy、默认“项目优先”规则及规则 API/UI。
6. 增加存量扫描、dry-run、选择执行、批次恢复与逐项撤销。
7. 更新 ArchivePage、API 契约与部署配置，最后运行单元、集成和前端测试。

---

## 6. 风险与缓解

| 风险 | 缓解 |
|---|---|
| LLM 分类漂移（同类文件归不同目录） | 目录画像随归档增长自我校正; 低置信统一等待确认; 历史 + undo 事后修正 |
| 自动模式误归档 | 单阈值拦截低置信样本; 全操作可撤销; review_all 先行观察再放开 |
| move 成功但知识分析失败（双态不一致） | journal 关联 kb_doc_id 并记录失败，可重试；文件本体优先于索引 |
| LLM 滥用 new_subdirectory 致目录树膨胀 | 新目录必须通过路径 Validator；policy 可关闭自动建目录，后续补目录合并/重命名 UI |
| 并发扫描与人工操作竞争同一文件 | 队列 claim 语义 + planner 源指纹复核 |
| workspace 卷单点 | compose 管理卷随既有备份策略; hash 双记录可校验完整性 |

## 7. 后续演进（本次不做）

- 规则引擎前置（Rules First, 未匹配再走 LLM）——省 token、提速。
- 留置文件的定期自动重分类（画像丰富后可能重新过阈值）。
- 目录树人工管理（重命名/合并 + 自动迁移文档归属）。
- 归档文件在线预览/下载。
- 跨文件实体上下文与反馈学习（调研文档 V4/V5）。
