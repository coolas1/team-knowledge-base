# Proposal: add-auto-file-archiving

## Why

团队知识库目前是 text-first 的：上传的文件被提取文本入库后，原始文件本体只落在
`uploads/<doc_id>/`（容器可写层，未挂卷），LAN 部署每轮 CI 重建容器即丢失，
`documents.file_path` 悬空。同时知识库文档没有任何目录/分类结构，团队成员
既没有可靠的文件保管，也没有自动整理能力。

基于 `docs/AI_Auto_File_Archiving_Source_Level_Research.html` 的调研结论
（Watcher → Queue → Extract → Retrieval → LLM → Plan → Validate → Execute →
Journal/Undo），在现有引擎上增加自动文件归档：文件进入收件箱后由 AI 分类、
默认自动归档到持久目录树；文件单次提取后，归档决策与现有 GraphRAG 知识
分析并行。高置信度自动执行，低置信度带着已有目录/新目录建议进入用户确认；
同时提供可编辑规则与 Sortio 风格的存量文档批量归档。

## What Changes

- 新增持久 workspace 卷（`inbox/` 收件箱 + `archive/` 分类目录树），挂载进
  webapp 容器；`uploads/` 一并迁入卷，修复原始文件随容器重建丢失的问题。
- 新增两个文件入口：Web 上传（现有"上传文件"按钮改传 inbox，走归档流水线；
  原 `/api/documents/upload` 直接入库接口保留兼容）与宿主机共享目录投放
  （BFF 内轮询扫描器自动发现）。
- 新增归档流水线：扫描器（轮询 + size/mtime 稳定性检查 + sha256 去重 +
  临时文件过滤）→ `archive_jobs` 持久队列（claim/lease/retry，复刻
  graph_outbox 模式）→ 文本提取（复用 `extractors/registry`，只提取一次）。
  同一 FileContext 并行进入：(a) 候选目录检索 + LLM 分类；(b) 原 GraphRAG
  overview/chunk/embedding/图谱分析。
- 分类不是“有目录就复用、没目录才新建”的单向兜底。每个文件无论目录树是否
  为空，都同时比较“复用 Top-K 已有语义目录”和“创建新的项目/主题目录”两类
  方案；已有候选不合适时必须允许提出新目录，空目录冷启动时必须提出有语义的
  新目录，不能默认堆入“待整理/未分类”。
- 简化为二态分流：`confidence ≥ threshold` 自动执行，否则进入待确认队列。
  高置信分类允许按 policy 自动新建目录；低置信分类必须提供已有目录或新目录
  建议。用户可确认、改分类或暂不归档（文件一直留在 inbox）。
- 新增 Planner + Validator（工作区边界 / 路径穿越 / 目标冲突 / 权限检查），
  LLM 不直接操作文件系统，只产生结构化决策。
- 新增 Executor + Operation Journal：执行 move 后写日志，支持反向 move 的
  撤销；知识索引与归档并行形成，move 后只更新 `documents.file_path`，不重复
  ingest。默认撤销归档不删除知识索引。
- 新增版本化归档 policy，默认“项目优先”，用户可修改；每个计划记录使用的
  policy 版本。
- 新增存量归档：扫描已有未归档 Document，生成 dry-run，用户全选或部分选择
  后批量 move + journal + 更新原 Document.file_path，不重建 chunks/图谱。
- 新增前端"归档"页：待确认（确认/暂不归档/改分类）、暂未归档、存量归档、
  规则、历史台账与目录树浏览。
- 配置化：阈值、轮询间隔、自动/审核模式、存量归档开关等进入 `config/app.yaml`
  与 settings。

## Capabilities

### New Capabilities

- `auto-archiving`: 自动归档流水线行为——inbox 发现与稳定性判定、去重、
  持久任务队列与重试、候选目录检索、LLM 结构化分类、二态置信度分流、
  安全校验、执行与操作日志、撤销、归档与知识库入库的联动。
- `archiving-review`: 人工审核面——BFF 归档 API（待确认/暂不归档/改分类、
  policy、存量批次、历史/撤销/模式）与 SPA 归档页。

### Modified Capabilities

- `app-deployment`: 部署形态变化——新增 workspace 持久卷挂载（inbox +
  archive + uploads 迁入），归档相关环境变量/配置注入 compose；原始文件
  从易失容器层变为持久存储。

## Impact

- **engine**: 新增 `src/engine/components/archive/`（或同级模块）：扫描器、
  jobs 队列（新表 `archive_jobs`）、分类器、planner/validator、executor、
  journal（新表 `archive_operations`）；复用 `extractors`、`embedder`、
  `analyzer` 的调用范式。
- **frontend BFF**: 新增 `routes_archive.py`；`deps.py` 启动扫描器/归档
  worker；上传路由增加 inbox 入口。
- **frontend SPA**: 新增 ArchivePage + 导航入口；上传按钮行为变更；
  api-client 扩展。
- **部署**: `docker-compose.yml` webapp 增加 workspace 卷；`.env.example`/
  settings/config 增加归档配置项。
- **数据库**: 两张新表（`archive_jobs`、`archive_operations`），
  `init_db` 自动建表；目录画像可复用现有 documents 数据，无需新表起步。
- **不受影响**: 检索/问答/图谱/版本链/MCP 现有行为不变（归档入库走的
  就是同一条 ingest 流水线）。
