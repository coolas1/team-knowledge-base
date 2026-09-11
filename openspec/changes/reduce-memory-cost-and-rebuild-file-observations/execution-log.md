# 分批执行记录

## 基线

- 分支：feat/memory-consolidation
- 开始 HEAD：1b629d7a7a11338c3d8751a13585d6317ce658e2
- 上轮检查：501 passed / 32 skipped，ruff passed。仅作基线，不替代本轮验收。
- 用户已有改动，不随批次暂存：docker-compose.yml、.gitattributes、output/，以及 hindsight_components/backfill.py、hook.py、neo4j_graph.py 的既有工作区差异（当前 git diff 无实质代码差异，保留其文件状态）。
- 上轮摘要初版待批次二吸收：components/analyzer.py、engine/config.py、graphrag/backend.py、pipeline.py、tests/engine/test_build_engine.py、test_pipeline.py。
- 上轮缓存初版待批次三吸收：hindsight_components/config.py、query.py、recall.py、reflect.py、service.py、fact_cache.py、tests/engine/test_fact_cache.py。
- 混合配置 config/app.yaml、config/schema.py 按批暂存相关 hunk；docs/memory-cost-controls.md 随最终能力更新，不提前宣称迁移完成。
- 本 change 计划文件随第一批提交。工作区原有内容不回滚、不批量暂存。

## 差距与任务映射

| 契约/风险 | 基线现状 | 对应任务 |
|---|---|---|
| 摘要身份与复用 | overview 无源/模型/模板版本，重试无法可靠复用 | 1.3、2.2 |
| 全文件入口 | 上传/编辑初版已分离，backfill 和 snapshot replay 仍待覆盖 | 2.1–2.4 |
| cache 容量/TTL | 初版有边界，缺续期、指标和完整过滤归一化验证 | 3.1–3.2、3.5 |
| cache 当前权限与版本 | 单实例初版校验，需多 worker 和迁移失效测试 | 3.3 |
| 选择性证据 | 初版支持子集，需完整审阅兼容与预算测试 | 3.4 |
| 旧文件派生内容 | 未执行清理；必须沿 ObservationEvidence，而非 observation.document_id | 4.1–4.5 |
| 并发与恢复 | repository 已有 revision、evidence invalidation、outbox；待迁移接入 fencing | 5.1–5.5 |
| 实际历史数据 | 未识别目标、未迁移 | 6.1–6.7 |

核对依据：repository._invalidate_observation_evidence 会令 evidence inactive、observation stale；仅此不足以保证旧正文彻底不可作为证据，清理批需进一步保证活跃结果消失。已有 memory-consolidation delta 要求保留审计历史与禁止旧事实写回，迁移沿用该契约。

## 批次一（进行中）

- 新增独立 file_summaries 表，以 document + identity key 保存源 hash、标题 hash、模型、策略、模板、预算、coverage、成功/失败状态。通过实时 Document scope 鉴权，无需为旧文件虚构摘要；晚到失败不覆盖成功。
- 新增幂等 DDL migration，接入 init_db。新增合成文件/对话/混合 observation fixture 和单元、PostgreSQL 升级集成测试。
- 验收命令与实际结果待补；未提交。
- 环境：开始时 SCOPE_TEST_DSN 未配置、localhost:5433 不可达、Docker daemon 未运行；用户已告知正在启动 TKB，等待隔离数据库测试。

### 第一批验收

- `.venv/Scripts/ruff.exe check`：通过。
- `.venv/Scripts/python.exe -X utf8 -m pytest tests src/engine/hindsight_components/tests -q --basetemp output/pytest-summary-batch1`：506 passed / 33 skipped。
- 独立临时 pgvector:pg16 容器，127.0.0.1:46639/summary_test，无现有数据挂载、tmpfs 数据目录；每次集成测试再使用随机隔离 schema。
- `RUN_INTEGRATION=1` + 本机测试 DSN 执行 `tests/integration/test_file_summary_migration.py`：1 passed，覆盖旧 documents 表升级两次、摘要复用、跨 bank 隔离、原文保留及迟到失败不覆盖成功。
- 测试数据库升级已通过；提交 SHA 在提交后追加。

第一批提交：`979acc7`，五项任务均已完成。

## 批次二验收

统一 FileSummaryManager，pipeline 先持久化摘要后 retain 按当前原文 identity 复用。retention preprocess 覆盖 backfill/replay，摘要策略写入 extraction context，策略变化不能继承全文 provenance。原文变化冲突拒绝旧输入。

- ruff：通过。全库及内部记忆测试：516 passed / 33 skipped。
- 文件入口矩阵：10 passed，覆盖三种 source_type × replay，已迁移策略保持、对话/显式旧模式、摘要失败不发布。
- 独立 PostgreSQL 升级+manager 复用/变化/失败恢复：1 passed。
- 暂存按功能拆分共享 config/service/query 文件，缓存部分留给第三批。

第二批提交：`51294d7`；暂存快照独立验收 512 passed / 33 skipped（不包含第三批未暂存缓存测试），ruff passed。

## 批次三验收

- 规范化 bank/scope/filter key，timeout 与结果预算不作为缓存身份，当前请求预算单独限制；有效使用续期、LRU、TTL、容量 0、源块排除、命中/过期/驱逐计数。
- 使用 load_cached_facts 批量读取当前权限、类型、标签、时间过滤及 memory_version；两个 worker 修改/删事实和修改文档可见标签后旧内容失效。
- 固定 follow-up fixture：冷请求 1 次 recall，热请求新增 0 次 recall、cache hits=1，答案及引用不变。这是工具调用测试，不是实际 DeepSeek 账单节省率。
- 全库及内部记忆测试：519 passed / 34 skipped，ruff passed。独立 PostgreSQL 多 worker 失效测试：1 passed。

第三批提交：`14b5aaa`。

## 批次四验收

- 新增持久化 FileRebuildRun 与 preview/plan/retire/restore CLI；manifest 固定数据库身份、源 hash、revision、完整来源闭包。
- 恢复材料导出带 checksum；未导出不能清理。retire 将旧 facts/observations 设 retired，清空旧 observation 当前文本、tombstone observation head，保留审计历史；原文、对话、非目标事实不变。
- PostgreSQL + 独立 Neo4j 演练发现原 graph_projection 未过滤 inactive 行，已修复为只投影 active memories / 链接目标；真实图清理和既有图契约 2 passed。
- 从实际导出文件恢复、拒绝后续写入后的恢复：1 passed；fixture 验证纯文件/跨文件/混合来源和派生 mental model。
- 全库及内部记忆测试：519 passed / 35 skipped；ruff passed。未在目标 TKB 运行清理。

第四批提交：`565de81`。

## 批次五验收

- FileRebuildRunner 串行分批：持久化摘要成功、旧内容退休、retain 提交、consolidation、图投影和 verified/empty 阶段；同一 run 用 PostgreSQL session advisory lock 互斥。
- 每次生成调用前持久化保守 token/cost 预留，成功按 provider usage 结算，中断/失败保留预留；缺失 usage 明确记录估算。预算耗尽不发布空抽取结果。摘要模型/策略变化要求重规划。
- 摘要成功后、清理后、retain 已提交但未记录进度时分别故障注入并续跑：各完成两文件；摘要调用两次、抽取调用两次，重放不重复调用。混合来源的新 observation 引用保留的对话事实；非目标 observation 指纹一致。
- 旧 revision 的 worker 被拒绝；后续 fact version 写入不能被恢复覆盖；不同 scope 的 consolidation job 保持 pending、不被迁移领取。存在同 scope 非目标待处理事件时在清理前等待。
- 完整 PostgreSQL/Neo4j 集成回归 `tests/integration/test_memory_scope_migration.py`：31 passed；最终迁移专项 4 passed。独立测试容器采用 tmpfs，随机 schema/bank，与目标实例隔离。
- 回归适配：旧全文 append/cache/权限契约测试显式设置旧模式；队列测试按既有“保留有效 lease、新水位随后消费”契约模拟租约过期；mental model enqueue 使用数据库时间，避免 Windows 与 Docker 时钟差异导致新任务暂不可领取。
- 全库 ruff passed；`pytest tests src/engine/hindsight_components/tests`：519 passed / 38 skipped。跳过项由集成环境开关控制，适用真实数据库用例已单独执行。
- 本批提交 SHA 待提交后记录。第六批目标预览已经核对，但未对目标运行清理或生成模型调用。
