# Tasks: add-auto-file-archiving V2

## 1. 共享提取与并行编排

- [ ] 1.1 新增不可变 `FileContext`（raw_text/content_hash/file metadata），
  scanner 判稳后只调用一次 extractor。
- [ ] 1.2 扩展 `IngestSource`/Pipeline，使知识分析可直接消费可信
  `extracted_text`，禁止 workspace 来源再次读取和提取文件。
- [ ] 1.3 协调器并行启动归档分类与知识分析；测试两分支失败相互隔离、
  move 不影响 chunk 分析、最终 `documents.file_path` 一致。
- [ ] 1.4 归档分支不得重复调用 `kb.ingest`；低置信文档先关联 inbox 路径，
  批准移动后只更新路径。

## 2. 二态决策与安全执行

- [ ] 2.1 将分流简化为 `confidence >= threshold -> auto`，否则
  `awaiting_review`；删除 delta/skipped 第三分支及相应配置。
- [ ] 2.2 允许高置信决策按 policy 自动创建新目录，仍需通过工作区边界、
  路径穿越、文件名、冲突、源 hash 与权限校验。
- [ ] 2.3 低置信计划必须包含推荐已有目录或新目录；UI 提供确认、改分类、
  暂不归档。
- [ ] 2.4 将 reject 改为 defer：job 状态为 `unarchived`，文件原地留在
  inbox，可重新规划或手动归档。
- [ ] 2.5 调整 undo：默认只反向 move 并更新 `documents.file_path`，不删除
  知识索引；“撤销并删除知识”作为显式独立动作。
- [x] 2.6 分类时始终对称比较“复用 Top-K 已有目录”和“创建新语义目录”；
  已有候选不合适时可新建，空树时按项目/主题冷启动。
- [x] 2.7 `待整理/待确认/未分类` 等操作性兜底目录不进入语义 Top-K；模型
  不可用时返回可重试、不可执行的失败，不生成伪分类计划。

## 3. 可编辑归档规则

- [ ] 3.1 增加版本化 `archive_policies` 数据模型，保存 instructions、
  priority、match/template、allow_new_directory 与 fallback。
- [ ] 3.2 提供默认“项目优先”policy：识别明确项目时使用
  `项目/<项目名>`，否则回退目录画像语义检索。
- [ ] 3.3 分类 prompt 带入当前 policy，并把 policy_id/version 写进 plan 和
  operation，保证决策可解释。
- [ ] 3.4 新增 `GET/PUT /api/archive/policies` 与前端规则编辑 Tab；规则修改
  只影响新任务和用户主动重新规划的任务。

## 4. 存量文档归档

- [ ] 4.1 增加 `archive_migration_batches`/items，记录扫描、dry-run、选择、
  执行、失败与重试。
- [ ] 4.2 扫描公开文档当前版本：识别可归档、已归档、原件缺失、路径冲突，
  默认 `legacy_archiving_enabled=false`。
- [ ] 4.3 按 policy 为存量文档生成 dry-run，不移动文件；前端支持全选、
  部分选择、改目录和取消。
- [ ] 4.4 批量执行逐文件 move + journal，并更新原 Document.file_path；
  保留 doc_id/chunks/Neo4j，不重新入库。
- [ ] 4.5 原件缺失只报告；Markdown/TXT 的 raw_text 重建必须是显式操作。
- [ ] 4.6 新增 `/api/archive/legacy/scan|plan|execute` 与“存量归档”Tab，
  测试重启续跑、部分失败和逐项撤销。

## 5. 队列、API 与前端收口

- [ ] 5.1 更新 ArchiveJob 状态机与 kb_doc_id/policy 字段，保留 claim、lease、
  attempts、指数退避和 dead 语义。
- [ ] 5.2 更新归档 API：reviews approve/defer/reassign、unarchived replan、
  policies、legacy batches；移除 skipped API。
- [ ] 5.3 ArchivePage 调整为待确认、暂未归档、存量归档、规则、历史/目录树；
  用语中不再出现“拒绝后返回 inbox”。
- [ ] 5.4 更新配置、compose、README 与设计文档，移除 delta；保留
  review_all 作为上线观察开关。
- [x] 5.5 将目录画像的扁平路径渲染为可展开/收起的层级目录树，并补齐
  API 未显式返回的中间目录。
- [x] 5.6 目录树 API 返回目录下的知识库文档，前端显示文件类型、索引状态，
  并复用现有 `/documents/{id}` 详情页完成内容跳转。

## 6. 验证

- [ ] 6.1 单测：共享提取仅执行一次、双分支真正并发、二态阈值、新目录自动
  创建、defer/replan、policy 版本。
- [ ] 6.2 集成测试：新文件高置信自动归档且可检索；低置信未移动但可检索，
  批准后路径更新且不重复 ingest。
- [ ] 6.3 集成测试：存量扫描 -> dry-run -> 部分选择 -> 批量归档 -> undo，
  验证 doc_id、chunks 和图谱保持不变。
- [ ] 6.4 `uv run ruff check`、`uv run pytest`、前端 `npm test` 全绿。
- [ ] 6.5 单测：已有目录复用、已有但不匹配时新建、空目录新建、多文件冷启动
  不汇入兜底目录、禁用自动新建时仍保留人工确认建议。
