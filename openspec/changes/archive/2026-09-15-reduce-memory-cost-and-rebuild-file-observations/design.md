## Context

动机见 proposal.md。当前分支为 `feat/memory-consolidation`，工作区已有上一轮的摘要索引/缓存初版以及用户的其他改动。已有代码测试记录为 501 passed / 32 skipped，仅作基线；历史 backfill 仍可能将全文送入 retain，摘要未具有完整的持久化策略身份，历史 observation 未迁移。

现有存储拥有 facts、ObservationEvidence、ObservationRecord、历史版本、consolidation 任务、graph outbox 和 mental model 依赖。另一个活动 change `align-hindsight-memory-capabilities` 定义来源删除传播与历史可查，本 change 复用这些约束。

## Goals / Non-Goals

**Goals:** 所有文件入口采用同一摘要身份；历史旧全文事实无法再生成 observation；删除后的恢复可核验；每批形成可独立验收的提交。

**Non-Goals:** 不重写对话 consolidation，不引入 Redis，不物理删除文件/原文向量，不清空整个 bank，不以本次计划触发线上操作。

## Decisions

### 1. 摘要是有版本的文件派生物

摘要身份包含 bank/document、原文 content hash、摘要策略版本与模型/提示模板标识，保存摘要及覆盖范围。匹配且生成成功时复用；策略或原文改变才重新生成。当前单次最多 24,000 字符输入、2,000 字符摘要为默认预算，长文均匀抽样并标注不完整覆盖；摘要失败不能将占位错误文本作为 fact。未配置 LLM 的摘录必须标记其类型。

文件全文始终保存和向量索引，摘要才进入 retain。上传、编辑、backfill、retry/reprocess 共用策略判定，不能通过重放旧全文 snapshot 绕回旧模式。旧逐块图谱模式保留显式开关，迁移过的文件不能因普通重试默默回到旧策略。仅靠 overview 字符串无法判断是否可复用，故增加持久化身份。相比全文 map-reduce，总预算可预测；细节交给原文检索。

### 2. Cache 是有界工作集，不是事实权威

保留已通过相关性门槛并被选入结果的 world/experience facts，以及按需展开选中的 facts。默认容量 256、闲置 TTL 1800 秒，LRU 驱逐；每条及总上下文都有 token 预算。进程内由长期 service 持有，scoped clone 共享；key 包含规范化 bank、可见范围和影响结果的过滤条件，不能用不同请求的临时对象身份代替真实 scope。瞬时 timeout 不作为内容身份，归一化后仍执行当前请求预算。

命中使用当前有效性/版本读取重新鉴权；更新、删除、迁移、权限变化均不得返回旧文本。跨进程以持久化版本校验兜底，驱逐不删除数据库 facts。相关性不满足时正常检索，缓存失败降级；模型必须实际收到命中的文本和来源，而不只是 ID。默认 observation 搜索不全量带 source_facts，按当前问题展开并暴露截断与补查方式。相比完整响应缓存，工作集允许相近问题复用且保留逐事实失效。

### 3. 按证据闭包删除历史 observation

迁移目标是持有旧文件全文提取策略的 document/fact revisions。通过 source provenance、保留快照及文件记录交叉识别，不能仅凭 file_type 或 observation.document_id；来源不明进入报告并阻断其自动删除。

先从目标旧 facts 沿有效 ObservationEvidence 求受影响 observation，再检查所有来源：

- 纯文件 observation：删除其当前活跃内容、有效 evidence 和检索/图投影，随后从新摘要事实重新生成。
- 文件与对话/其他文件混合：删除旧 observation 的当前内容并解除旧证据；保留非目标来源原始事实，重建结果仅由剩余有效事实与新摘要事实导出。不能只删除一条 evidence 而保留含旧信息的正文。
- 与目标文件无证据关系的对话 facts / observations 完全保留。

“删除”指从活跃记忆、常规检索和派生输入中移除；已有审计历史可受控保留，不作为有效证据回灌。该定义兼容现有历史可查规范。旧全文 facts 必须同步失效，旧抽取缓存/快照不能再次排队；相关 mental models 标记失效并重建，graph outbox 清理和 fact cache 失效可追踪。相比全库清空或逐文档 cascade，该方式保护跨来源证据。

### 4. 先准备，后切换；以文档 revision 和 scope 租约隔离并发

迁移 manifest 固定 bank、文档 ID、预期 hash/revision、策略版本、受影响闭包与目标环境标识。持久化每项阶段：planned → summary_ready → retired → retained → consolidated → verified，外加 failed/retry。网络 LLM 不放在数据库长事务内。

先生成并持久化摘要，再在受控事务中复核 revision、禁用旧 facts/observations、登记新 retain 和清理 outbox。旧 worker 的 read-set/version 提交必须被拒绝；暂停/排空目标 scope 的旧任务或用 generation fence 排除旧结果，其他 scope 可继续运行。完成新 retain 不等于迁移完成，必须等待 consolidation 和投影清理达到终态。变更冲突暂停该文档并重新生成计划，不覆盖用户的新编辑。

### 5. 六批提交与验收记录

按 tasks.md 顺序实施。每批运行适用单测和契约测试、全库 ruff 和 pytest，缓存/记忆内部测试目录须显式覆盖；数据库迁移需在隔离真实服务完成验证。逐文件/逐 hunk 暂存该批内容，不能 `git add .` 捎带用户改动；已存在代码按批复核吸收，不预先勾选。每批提交到 `feat/memory-consolidation`，标题采用 Conventional Commits；未通过不提交完成状态、不进入下一批。

SHA 在提交后记入执行记录并纳入下一批提交；最后一批追加独立审计记录提交，避免要求提交包含自己的 SHA。只要求本地 commit，不隐含 push、merge 或部署。实际数据清理在最后一批运行；目标实例在执行阶段从连接配置与只读身份检查确定，不在计划中猜测数据库地址。

## Risks / Trade-offs

- [抽样摘要遗漏细节] → 明确覆盖标记，保留全文检索，固定中长文对照样本检测关键细节能否从原文找回。
- [删 observation 后旧任务写回] → revision/generation fence、目标任务隔离、失败恢复测试及最终旧事实引用数为零的核验。
- [混合来源误删] → evidence 闭包、来源不明阻断、混合文件/对话 fixture 和迁移前后非目标校验和。
- [缓存跨 scope 或陈旧] → 规范化 key、每次当前有效性校验、权限变更/多 worker 测试。
- [迁移自身耗费额度] → 摘要复用、默认串行、可配置每批文档数及总 token/cost 上限；预算不足暂停且可续跑。
- [旧数据恢复时覆盖新写入] → 版本匹配才恢复，存在新写入时使用前向修复；审计输出不包含凭据或不必要的原文。

## Migration Plan

1. 批次一建立现状清单与持久化身份；批次二封堵所有旧全文入口。
2. 批次三完成缓存；批次四提供 dry-run、备份导出和范围可验证的清理器。
3. 批次五完成摘要重处理、失败续跑、并发 fencing 和隔离数据库演练。
4. 批次六先只读确认实例、生成完整 manifest 与受影响计数，校验备份可恢复，按小批次执行已授权的文件清理和重处理。每批设 token/cost 限额，失败保留作业状态而不是从头再调用模型。
5. 完成条件：目标旧文件事实 active=0；指向目标旧事实的有效 observation evidence=0；旧 observation 不可从检索/图/缓存返回；新摘要事实和 observations 可追溯；非目标内容校验和一致；待清理投影和本次任务达到终态。文档没有可提取事实时允许明确的 empty 终态，不能强制造假 observation。
6. 回滚：切换前失败保留旧有效数据；切换后失败保持旧内容不可用并优先续跑。仅在无新写入且版本匹配时从已验证备份恢复该 scope 的旧代，回滚后作业不标记成功。服务发布遵循 cicd 管线，不手动 compose up。
