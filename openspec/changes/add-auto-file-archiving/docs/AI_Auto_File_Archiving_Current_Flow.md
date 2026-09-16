# 当前自动归档流程说明

> 本文描述当前代码已经实现的 V2 流程，重点说明：新文件如何进入系统、归档和知识库如何并行、低置信度如何处理、已有文档如何批量归档，以及归档后如何检索和问答。

## 1. 一句话概括

系统现在分成两个并行分支：

1. **归档分支**决定文件应该放在哪个物理目录、是否改名，以及是否可以自动执行。
2. **知识库分支**把文件内容加工成 overview、chunks、实体、关系和向量，使文件可检索、可问答。

两个分支只在开始时共享一次文本提取结果，之后各自完成自己的工作。

```text
文件进入 inbox
      |
      v
稳定性检查 + SHA256 去重 + ArchiveJob 入队
      |
      v
extract 一次，生成 FileContext(raw_text / hash / 文件信息)
      |
      +-------------------------------+
      |                               |
      v                               v
归档分支                         知识库分支
目录画像向量检索                 整篇 overview/file_relations
LLM 整文件分类                   每个 chunk 的 entities/relations
Planner / Validator              每个 chunk 的 embedding
二态置信度分流                   Postgres + Neo4j
      |                               |
      +---------------+---------------+
                      v
          文件可检索、可问答，且有物理归档位置
```

## 2. 新文件的完整流程

### 2.1 文件进入 inbox

文件有两种入口：

- Web 页面上传到 `POST /api/archive/inbox`；
- 用户或同步程序直接把文件放进 workspace 的 `inbox/` 目录。

扫描器不会立即处理正在写入的文件。它会过滤临时文件，例如 `.part`、
`.crdownload`、隐藏文件，并连续检查文件大小和修改时间，确认文件稳定后才创建 `ArchiveJob`。

`ArchiveJob` 使用文件 SHA256 做去重，并记录 claim、lease、attempts、失败重试和 dead 状态，因此服务重启后可以继续处理未完成任务。

### 2.2 只提取一次

worker 对源文件调用 extractor registry，生成一份共享的 `FileContext`：

```text
FileContext
├── raw_text       提取后的全文
├── content_hash   内容指纹
├── file_name      原始文件名
├── file_path      inbox 中的路径
└── file_type      markdown/pdf/docx/...
```

归档分类只使用 `raw_text` 的前 3000 个字符作为摘要；知识库流水线使用完整 `raw_text`。

知识库通过 `IngestSource.extracted_text` 接收这份文本，因此不会再次从路径读取和提取文件。这样可以避免：

- PDF、DOCX 等格式重复解析；
- 文件移动后知识流水线找不到原路径；
- 归档分类和知识分析看到不同版本的内容。

### 2.3 两个分支并行启动

#### 归档分支

归档分类器执行以下步骤：

1. 根据已归档文档的 overview 构建目录画像；
2. 用摘要 embedding 检索 Top-K 候选目录；
3. 将文件名、前 3000 字符、目录画像和当前 policy 交给 LLM；
4. LLM 返回结构化决策：

```json
{
  "candidate_id": "项目/知识库",
  "new_subdirectory": null,
  "new_name": "知识库架构说明.md",
  "confidence": 0.91,
  "rationale": "内容属于知识库项目设计文档"
}
```

LLM 只能引用候选目录，或者提出一个新目录建议，不能直接操作文件系统。

#### 知识库分支

知识库调用原有 GraphRAG pipeline：

```text
完整 raw_text
   ├── 整篇文档 -> overview / file_relations
   ├── chunk 1 -> entities / relations / embedding
   ├── chunk 2 -> entities / relations / embedding
   └── chunk N -> entities / relations / embedding
```

结果写入：

- Postgres `documents`；
- Postgres `chunks`；
- Neo4j 文档、实体、关系图；
- chunk embedding，用于后续检索。

知识库分支会先创建一个 `kb_doc_id`，其 `documents.file_path` 暂时指向 inbox 文件。归档分支不需要等待所有 chunk 分析完成即可拿到这个 ID。

## 3. 置信度分流

当前只有两种业务状态，不再使用“高、中、低三段带状分流”：

```text
confidence < threshold                 confidence >= threshold
        |                                      |
        v                                      v
 awaiting_review                         auto
 推荐目录，等待用户确认                 自动执行 Planner/Validator 后 move
```

默认阈值是 `0.75`，可以在配置中调整。旧配置里的 `delta` 字段只为兼容旧配置，当前分流不再使用它。

### 3.1 高置信度

高置信度可以：

- 放入已有目录；
- 根据 policy 允许创建新目录；
- 按 LLM 建议重命名。

Planner / Validator 会再次检查：

- 目标必须位于 `archive/` 内；
- 不允许路径穿越；
- 文件名合法；
- 同名文件按 suffix 或 block 策略处理；
- 源文件 hash 没有变化；
- 目标目录可写。

校验通过后执行：

```text
move(inbox/file -> archive/目标目录/file)
      |
      v
写入 ArchiveOperation
      |
      v
更新 documents.file_path = archive/目标目录/file
```

如果知识库分支已经创建了 `kb_doc_id`，这里不会再次调用 `kb.ingest`，只更新原文档路径。

### 3.2 低置信度

低置信度不会移动文件，而是进入 `awaiting_review`：

- 文件仍在 inbox；
- 知识库分支已经可以继续建立索引；
- 页面展示推荐目录、新目录建议、置信度和理由；
- 用户可以确认、改分类，或者暂不归档。

用户确认后，使用和自动路径相同的 Planner、Validator、Executor 和 ArchiveOperation。由于 job 里已经保存了 `kb_doc_id`，确认时只更新 `documents.file_path`，不重复 ingest。

用户选择“暂不归档”后：

```text
ArchiveJob.status = unarchived
ArchiveJob.routing_reason = deferred
文件仍位于 inbox
```

这不是“先归档再退回”，而是从未移动过。之后既可以手动选择目录，也可以
调用 `POST /api/archive/unarchived/{job_id}/replan`，使用当前最新版 policy
重新进入规划队列。

## 4. 撤销归档

撤销只撤销物理文件组织，不默认删除知识：

```text
校验 archive 文件仍存在
      |
校验当前 hash == ArchiveOperation.content_hash
      |
反向 move(archive -> 原 inbox/legacy 路径)
      |
更新 documents.file_path
      |
标记 operation.undo_status = undone
```

因此文件回到 inbox 后仍可检索、可问答。只有用户明确执行“从知识库删除”时，才调用知识库删除接口。

## 5. 默认归档规则

系统创建第一版默认 policy：

```text
项目优先：
1. 能识别明确项目时，优先归档到项目目录；
2. 无法识别项目时，使用目录画像语义检索；
3. 新目录最大层级由 `max_directory_depth` 控制，默认两级；
4. 是否允许自动创建新目录由 allow_new_directories 控制。
```

规则保存在 Postgres 的 `archive_policies` 表中，每次修改生成新版本。新的任务和用户主动重新规划的任务使用新版本；已经生成的计划和操作日志保留自己的 `policy_id` 与 `policy_version`，方便解释历史决策。

前端 `/archive` 的“规则与存量”页面可以修改规则，保存后不会回溯修改已经完成的归档。

## 6. 已有文档的批量归档

对于已经进入知识库、但物理文件还没有放进 `archive/` 的文档，系统提供 Sortio 风格的人工确认流程。

```text
GET  /api/archive/legacy/scan
          |
          v
列出未归档文档、路径和原件是否存在
          |
POST /api/archive/legacy/plan
          |
          v
按当前 policy 生成 dry-run 预览
          |
用户全选或部分选择，并可修改目标目录/文件名
          |
POST /api/archive/legacy/execute
          |
          v
逐文件 move + 更新 Document.file_path + 写 ArchiveOperation
```

存量归档不会重新切 chunk、重新生成 embedding 或重新写 Neo4j。它复用原有 `Document`、`chunks` 和图谱，只改变原文件的物理路径。

批次记录保存在 `archive_migration_batches`，包含 policy 版本、预览方案、已完成项和失败项。原件缺失的文档只报告，不会伪造原始文件。

## 7. 检索和问答如何使用归档后的数据

归档和知识库不是两套检索系统：归档只是管理原始文件位置，真正的检索和问答仍然使用原有 GraphRAG 数据。

```text
用户问题
   |
   v
query / recall
   |
   +--> chunk embedding 相似度检索
   +--> 关键词/重排
   +--> Neo4j 实体关系扩展（deep 模式）
   |
   v
返回相关 chunks、来源文档和可选 LLM 答案
```

区别是：

- **检索**负责找到相关 chunks 和文档来源；
- **问答**在检索结果之上，再调用 LLM 组织自然语言答案；
- 两者使用同一套 documents/chunks/embedding/Neo4j 数据，但问答比纯检索多一步答案生成；
- 归档目录画像使用的是目录分类专用 embedding，不等同于问答检索用的 chunk embedding。

## 8. 当前代码对应关系

| 作用 | 主要实现 |
|---|---|
| inbox 扫描与队列 | `src/engine/components/archive/scanner.py`、`jobs.py` |
| 一次提取与并行协调 | `src/engine/components/archive/worker.py` |
| 目录画像和 LLM 分类 | `src/engine/components/archive/classifier.py` |
| 计划和安全校验 | `src/engine/components/archive/planner.py` |
| move、路径更新和日志 | `src/engine/components/archive/executor.py` |
| 撤销 | `src/engine/components/archive/journal.py` |
| policy 版本 | `src/engine/components/archive/policy.py` |
| 存量扫描和批次执行 | `src/engine/components/archive/legacy.py` |
| 原有知识处理 | `src/engine/graphrag/backend.py`、`pipeline.py` |
| 页面和 API | `src/frontend/webapp/client/src/pages/ArchivePage.tsx`、`routes_archive.py` |

## 9. 当前可测试入口

打开：

```text
http://localhost:8000/archive
```

在“待确认”中测试低置信度流程，在“规则与存量”中测试 policy 编辑和已有文档的扫描、预览、批量执行。
