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

第五批提交：`2457708c`。

## 批次六准备状态（尚未执行目标清理）

- 2026-09-11 只读核对本机 TKB：PostgreSQL `localhost:5433/knowledge_base`，schema `public`，角色 `kb_user`；唯一 bank `default-team`，1 PDF + 10 conversations。文件来源 active facts 272、文件 observation 56、混合 observation 1；对话来源 facts 45、observation 13。
- 最终 dry-run：1 target、57 affected observations、ambiguous=0。明细在非提交文件 `output/rebuild-target-manifest.json`，不在 Git 放入文档标题、原文、恢复内容或凭据。
- 仅新增摘要和迁移侧表、建立 planned run：`8f3854e9-eff0-4ec7-b479-4653d878d5d0`；恢复材料 `output/rebuild-recovery-8f3854e9-eff0-4ec7-b479-4653d878d5d0.json`，checksum `243fca393d9c9f49eaa13d2b4eeaabe3619286b83fbfe7dd04da21f5b677c799`。没有在目标 retire 或调用生成模型。
- 使用目标实际备份在独立 `summary_test` 随机 schema 中重放退休和恢复；恢复 snapshot 与目标导出逐项相等，临时 schema 已删除。结果 `output/rebuild-recovery-rehearsal.json`。目标业务数据未改写。
- 运行容器没有 FileRebuildRunner，且没有源码 bind mount。原镜像 `sha256:7dfd9a841893b377ec2553532f88fbb2f19610c339acbad3717ea066736166af`；因此不能把本地提交视为运行版本已升级。
- 标准 Containerfile 构建遇到 Docker Hub auth EOF。核对已有镜像的 pyproject、规范化 uv.lock 和全部前端输入（33 文件）与 `2457708c` 一致后，复用该精确本地镜像依赖/SPA，COPY 提交快照 src/config，离线构建 `team-kb-webapp:2457708c`，镜像 manifest `sha256:2fea7ce28caf6565067650211cece0c3c2d4f3a92ebb972851e242115f9f28b7`。未改 latest 或运行容器。
- 镜像离线 smoke：runner import 成功，vector_only=true，fact_cache_capacity=256。OpenSpec strict validate 通过。构建上下文来自 git archive，不含工作区无关修改/凭据/备份。
- 拟执行策略：代码 `2457708c`，batch_size=1，run 累计生成 token 上限 100000；每次调用前预留，达到上限暂停。尚无目标实际 provider usage 或费用节省结论。
- 当前待决：根 CLAUDE.md 要求 team-kb 由 pipeline 操作，禁止手动 compose up；已配置流程面向 Linux LAN 的 origin/main，当前运行的是 Windows Docker 本机实例。版本切换需用户明确授权本机例外或通过其发布流程完成。不自动 push/merge，不把“继续实现”解释成绕过部署约束。
- 6.2–6.7 保持未完成：虽已具备备份与恢复证明，目标版本切换、实际清理/摘要重处理、最终查询对照和六批最终审计尚未完成。


## 批次六执行验收（替代上方准备状态）

- 用户明确回复“授权”，批准本次 Windows Docker 本机版本切换例外；随后已实际完成升级、退休与重处理，无 push/merge/archive。
- run `8f3854e9-eff0-4ec7-b479-4653d878d5d0` 已 verified。页面 361 → 108：文件 facts 258 → 51，文件依赖 observations 57 → 11，对话 facts 33 与独立 observations 13 保持不变。
- 旧有效 memories/evidence/图节点均为 0，旧 ID cache 权威校验为空；新 observation 仅引用有效 facts。原文、14 原始向量块和非目标记忆指纹不变。所有后台队列完成，失败与待处理为 0。
- 恢复材料与独立恢复演练沿用上述 checksum；目标已有新写入，后续应前向修复，不能覆盖恢复旧状态。
- 运行问题修复：`82e6938c` 同名实体图投影；`b39ed586` DeepSeek 有界 JSON；`78ff7f9b`/`4460f1ac`/`c67ec38f` 有界输出及旧删除事件免综合；`b28c81f6`/`c5d410e8` 原始文件向量与精确关键词召回。
- 最终运行镜像 `team-kb-webapp:c5d410e8`，manifest `sha256:9d63c389d107b953b90eef7cb6e385c16d0f8904c95902c07bc314eb5278f3ab`；本地 latest 同步指向此镜像，原镜像保留为 `team-kb-webapp:base-7dfd9a841893`。源代码来自 Git 快照，依赖和前端输入核对一致，未包含无关工作区改动。
- 累计迁移生成调用 14 次，13 次 usage 合计 41677 tokens；首次失败 usage 缺失。成功摘要仅 1 次，续跑复用。预算计入 85024 tokens，保守保留失败预留，未提高 100000 上限。未配置价格，不能把 cost=0 当作免费。详见迁移报告。
- 实际 HTTP 文件尾部查询 Top-5 命中原文。真实 PostgreSQL 对话事实配合固定 planner：冷检索 1 次，热新增检索 0 次，命中 1 次，答案/引用有效；此控制实验无生成调用，不推算线上费用节省率。
- 最终全库单元/契约回归 526 passed / 39 skipped，ruff 通过；最终 PostgreSQL 模块 25 passed / 7 skipped（未设置 Neo4j 等专项开关），原始文件检索专项 1 passed。前序完整 PostgreSQL/Neo4j 回归 31 passed。独立临时测试容器已关闭，用户服务继续运行。
- 原始证据保存在非提交 output/：rebuild-final-verification.json、rebuild-api-verification.json、rebuild-provider-usage.json 与执行日志。操作说明与脱敏报告位于 docs/memory-cost-controls.md 和 docs/file-memory-migration-2026-09-11.md。


## 六批最终提交索引

| 批次 | 主提交 | 验收 |
|---|---|---|
| 1 摘要版本与模式 | `979acc7c` | 数据库升级、摘要身份与隔离测试通过 |
| 2 摘要 retain | `51294d77` | 原文向量保留，摘要抽取与复用通过 |
| 3 fact cache | `14b5aaaa` | TTL/LRU、权限及版本失效、证据按需展开通过 |
| 4 历史退休 | `565de81f` | 混合来源清理、图与缓存失效、恢复演练通过 |
| 5 可靠续跑 | `2457708c` | 故障续跑、预算、并发 fencing 和端到端验证通过 |
| 6 目标迁移 | `ebfd2c5` | 实际目标 verified、页面 108、原文召回与缓存对照通过 |

第六批运行修复提交见上节，最终运行代码 `c5d410e8`。OpenSpec strict validate 和最终 ruff 均通过。全部任务完成，当前分支 `feat/memory-consolidation`；未 push、merge 或 archive。备份与私有运行材料不进入提交。
