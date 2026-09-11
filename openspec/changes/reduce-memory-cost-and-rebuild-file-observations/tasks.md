## 1. 批次一：基线审查与摘要身份

- [x] 1.1 记录当前分支 `feat/memory-consolidation`、HEAD、逐文件 diff 归属及已有测试结果，建立 `execution-log.md`；验收：清楚区分上一轮实现与用户无关改动，未暂存无关文件。
- [x] 1.2 审查已有摘要、cache 代码与三个 delta spec 的差距，核对活动 memory-capabilities change 的 evidence/revision 接口；验收：差距矩阵逐项对应后续任务，不把现有代码直接计为完成。
- [x] 1.3 实现摘要策略身份与持久化字段（源 hash、策略/模板/模型身份、覆盖范围、生成状态）；验收：旧数据兼容读取，匹配身份可复用，策略改变失效，数据库升级测试通过。
- [x] 1.4 固定包含短文件、长文件尾部细节、对话、跨文件及文件/对话混合 observation 的脱敏 fixture；验收：清单可重放且源关系可断言。
- [x] 1.5 执行本批专项测试及 `uv run ruff check`、`uv run pytest`（受限环境可用同一 .venv 工具和工作区 basetemp），仅暂存本批内容并提交 `feat(engine): version file memory summaries`；验收：测试通过、提交在当前分支、执行记录保存命令和提交后 SHA。

## 2. 批次二：所有文件入口统一摘要记忆

- [x] 2.1 审查并补齐上传/编辑的原文向量索引与摘要 retain 分离；验收：完整原文和向量保留、默认逐块实体调用为零、记忆输入不是全文。
- [x] 2.2 接通摘要复用、输入输出预算、抽样覆盖标记和生成失败处理；验收：未变化文件和失败后续跑不重复成功摘要调用，长文尾部 fixture 进入采样，错误占位不成为 fact。
- [x] 2.3 将 backfill、retry/reprocess 与可靠 retain 重放统一到摘要策略；验收：各入口参数化测试证明旧全文 snapshot/抽取缓存不能重新生成全文事实。
- [x] 2.4 回归对话 retain 与显式旧图谱模式；验收：对话事实输入无变化，图谱模式兼容测试通过，迁移策略不会被普通重试静默覆盖。
- [x] 2.5 执行专项与全库 lint/pytest，提交 `feat(engine): retain files from summaries`；验收：完整入口矩阵通过，记录本批 SHA，前批 SHA 纳入执行日志提交。

## 3. 批次三：Fact cache 与按需证据展开

- [x] 3.1 审查并补齐 cache 准入、容量、每条大小、闲置 TTL 与 LRU；验收：虚拟时钟测试覆盖最近使用续期、过期、超限驱逐及容量 0，驱逐不删除存储 fact。
- [x] 3.2 接入跨请求 service 生命周期及规范化 scope/filter key；验收：同 scope 后续请求复用，跨 bank/权限/结果过滤不串用，timeout 变化不错误切分内容缓存。
- [x] 3.3 完成删除、版本变更、权限变化、迁移与多 worker 的当前状态校验；验收：上述变更之后旧文本不得进入提示，校验失败正常回退检索。
- [x] 3.4 完成 observation 按问题选择来源 facts、数量/token 限制和截断补查；验收：大量来源 fixture 仅返回预算内相关子集，完整审阅接口可用，模型收到缓存文本与出处而不只是 ID。
- [x] 3.5 增加命中/失效/驱逐及证据 token 观测，运行固定重复查询对照；验收：有效缓存减少重复工具调用或输入 token，冷查询/未命中仍正确，报告不包含秘密。
- [x] 3.6 执行 `uv run pytest tests src/engine/hindsight_components/tests` 与全库 ruff，提交 `feat(engine): cache recent relevant facts`；验收：全部适用测试通过并记录本批 SHA。

## 4. 批次四：旧文件 observation 清理器

- [x] 4.1 实现只读 dry-run 和可保存 manifest，识别旧全文策略目标、revision 与完整 evidence 闭包；验收：预览零写入、零生成模型调用，来源不明报告并排除自动删除。
- [x] 4.2 实现目标范围备份导出及恢复校验；验收：隔离数据库可以还原文件派生事实、observation/evidence 与作业状态，备份不提交到 Git。
- [x] 4.3 实现纯文件 observation 当前内容清理及旧全文 facts 失效；验收：活跃 observation、旧 facts 和有效 edges 按 manifest 归零，原文/向量未删除。
- [x] 4.4 实现混合来源 observation 清理与重算排队；验收：旧正文不再有效，对话与非目标文件原始 facts 保留，不能仅删 edge 却保留旧正文。
- [x] 4.5 联动 mental model 失效、graph outbox 清理和 cache 版本失效；验收：检索、模型上下文、图和缓存均无法返回旧文件派生内容，审计历史不参与再生成。
- [x] 4.6 在隔离真实数据库运行删除范围和恢复集成测试，执行全库检查，提交 `feat(engine): retire legacy file observations`；验收：非目标校验和一致且记录集成环境标识与本批 SHA。本批不对目标实例运行清理。

## 5. 批次五：摘要重处理与可靠续跑

- [x] 5.1 实现持久化迁移阶段与幂等键，先准备摘要再切换有效来源；验收：重复执行不生成重复活跃 facts，成功摘要不重复计费，empty 有明确终态。
- [x] 5.2 实现 revision/generation fencing、目标 scope 任务隔离与冲突重规划；验收：暂停旧 worker 后恢复提交被拒绝，并发编辑不被覆盖，其他 scope 不受阻。
- [x] 5.3 接入新摘要 retain → consolidation → 投影清理/重建的终态等待；验收：每个新 observation 可追溯至新摘要或保留的有效事实，retain 成功但下游未完成时不报告完成。
- [x] 5.4 实现批大小、串行默认、token/cost 预算暂停和 resume；验收：达到上限不启动额外模型调用，重启从检查点续跑。
- [x] 5.5 在摘要成功后、旧内容清理后、新 retain 后分别注入失败并恢复，验证版本保护恢复/前向修复；验收：无旧内容复活、无新写入丢失、无重复摘要调用。
- [x] 5.6 完成端到端隔离数据库演练及全库检查，提交 `feat(engine): resume file memory rebuilds`；验收：纯文件和混合来源 fixture 均迁移完毕，记录本批 SHA、阶段报告及恢复证据。

## 6. 批次六：目标历史数据分批清理、重处理和验收

- [x] 6.1 只读核对实际目标实例、bank、文档总数与连接身份，输出不含凭据的环境记录及最终 dry-run manifest；验收：目标明确且未误连测试/其他 bank，来源不明项目单列，不能猜测实例后执行。
- [x] 6.2 导出并校验目标范围恢复材料，记录运行策略版本、执行代码 SHA、批大小和 token/cost 预算；验收：备份恢复演练成功，代码已具备前五批能力。若目标服务缺少新代码，通过既有 cicd 发布流程满足条件，不隐含推送授权。用户已明确授权本次 Windows Docker 本机版本切换例外，证据见 execution-log。
- [x] 6.3 先执行一个小批次并核验，再按 manifest 处理剩余历史文件；验收：每批旧文件 observation 被清理、新摘要记忆完成重处理，失败有明细可续跑，不能只交付命令而未执行就勾选。
- [x] 6.4 核验全部目标旧全文 facts active=0、有效旧 evidence=0、旧图投影/缓存不可用，非目标对话与文件校验和保持一致；验收：保存可复查查询结果，所有迁移项 verified/empty，失败和待处理为零。来源不明尚未解决时本批保持未完成。
- [x] 6.5 运行固定文件细节查询、对话查询及重复 fact 查询对照，输出调用次数、真实 provider usage（缺失时明确标注估算）、累计 tokens、缓存命中和回答证据核验；验收：重复摘要调用为零、相关缓存用例上下文/重复检索减少、文件原文细节可找回，不宣称未测量的费用节省率。
- [x] 6.6 更新操作手册、回滚/续跑指令及脱敏迁移报告，执行全库检查与 OpenSpec strict validate，提交 `docs(engine): record file memory migration`；验收：提交仍在 `feat/memory-consolidation`，无备份/凭据/无关改动混入。
- [ ] 6.7 提交后将最终批 SHA 和六批验收索引补入 execution-log.md，单独提交 `docs(engine): finalize batch audit trail`；验收：`git log` 可逐一对应六批记录，所有 checkbox 均有实际证据后才勾选，未自动 push/merge/archive。
