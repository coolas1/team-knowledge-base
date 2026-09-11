## Why

文件全文参与逐块抽取与 consolidation 消耗大量 DeepSeek 额度，已有文件 observation 也会继续携带旧全文事实。需要将文件改为全文向量索引、摘要参与记忆，并将近期有用 fact 缓存；历史文件派生记忆必须清理并按新策略重建。

## What Changes

- 完善现有未提交实现：文件摘要复用、索引与记忆分离、覆盖上传/编辑/backfill/retry 等入口，增加可追溯的摘要策略版本与内容哈希。
- 建立有 scope 隔离、相关性筛选、容量/TTL/LRU 驱逐及变更失效的 fact 缓存，限制 observation 展开的证据预算。
- **BREAKING**：默认文件不再逐块抽取实体与全文 facts；原文和原文向量继续可检索，细节通过原文查询补充。
- 提供可预览、可恢复、分批且幂等的历史文件记忆迁移：删除旧文件派生 observation 的有效内容，清除旧全文 facts 的活跃资格与生成入口，以摘要 facts 重新 consolidation。跨文件/对话混合 observation 必须按证据来源重建，不能直接按所属 document_id 误删。
- 划分六个顺序批次，每批验收后提交到当前分支 `feat/memory-consolidation`，记录实际 commit SHA、测试与迁移结果；保留工作区无关改动。

## Capabilities

### New Capabilities

- `fact-working-cache`: 有界、有权限隔离与版本校验的近期 fact 复用及按需证据展开。
- `file-memory-rebuild`: 历史文件事实与 observation 的精确清理、摘要重处理、恢复及成本审计。

### Modified Capabilities

- `ingest`: 默认全文向量索引与摘要记忆分离；所有文件处理入口采用相同策略并保留原文。

## Impact

影响 engine 配置、analyzer/pipeline、Hindsight retain/backfill/recall/reflect、repository、consolidation/outbox、缓存及测试。可能新增摘要策略和迁移作业持久化字段，具体迁移采用仓库现有数据库升级机制。无需新增外部缓存服务。

依赖正在进行的 `align-hindsight-memory-capabilities` 中的 observation evidence、版本、租约机制，不重复定义该 change 的其他能力。前一轮代码属于待审查基线，测试通过不代表本计划任务已完成。本次只产出计划，不执行数据库删除、重处理或部署。
