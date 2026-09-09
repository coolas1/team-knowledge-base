# Hindsight 核心能力对齐矩阵

本矩阵以本地上游 Hindsight `9676fc169` 为固定行为参照，覆盖 OpenSpec
`align-hindsight-memory-capabilities` 的九份规格。TKB 继续使用现有
PostgreSQL、Neo4j、MCP、BFF 和 Pi 体系，没有引入第二套记忆存储。

| 能力 | 完整上游行为 | TKB 对齐结果 | 主要证据 |
|---|---|---|---|
| 范围与隔离 | bank、标签和类型过滤 | 可信 scope 贯穿文档、事实、图、任务、模型、Pi 会话；旧请求保持 `default-team` | 4 个隔离案例、真实 PostgreSQL/Neo4j、BFF/MCP 权限测试 |
| 事实分类与说话者 | 区分用户事实、Agent 建议/计划/行动 | retain 输入携带 speaker、agent、来源时间和时区；world/experience 与 modality 分开 | 8 个相同模型抽取案例，TKB 8/8 |
| 可靠保留 | 异步任务、幂等和处理状态 | 已完成轮次持久化 delivery intent；稳定 operation/request/revision；租约 fencing、补投、取消和降级状态 | 独立 Pi 进程故障演练、真实 MCP→PostgreSQL、4 个 failure 案例 |
| 跨轮持续归纳 | 新事实与已有 observation 持续比较 | consolidation outbox、范围 worker、版本化 create/update/delete、证据水位和恢复游标 | 15 个上游/TKB 同模型归纳案例及 PostgreSQL 生命周期门禁 |
| 重复合并 | 精确及可配置语义去重 | 归一化精确合并、语义近邻加等价判定，覆盖 create/update，保留证据并集 | duplicate 4/4，矛盾不误合并测试 |
| 矛盾、变化与删除 | 历史证据、新鲜度和派生失效 | observation 历史快照、冲突、stale、来源 tombstone、提交前版本复核和重算 | change 4/4、delete 4/4、删除竞态集成测试 |
| 检索与证据 | 类型/标签/时间过滤、预算、原文展开 | 四路检索共享范围、期限、候选和 token 上限；返回 freshness/provenance；expand 复核来源 | 旧/新契约 25 次比较、范围/删除/展开测试 |
| Mental models | 独立定义、刷新和长期综合认识 | 范围 CRUD、不可变版本、手动/事件/定时刷新、delta 校验与完整回退、删除 fencing | model 4/4、真实 PostgreSQL 版本发布与恢复 |
| 动态 Reflect | 工具循环按结果继续探索 | 五个严格工具、统一预算、stale 回查、actual citation 校验和一次修复 | reasoning 4/4、100 次确定性场景无失败 |
| Directives | 独立于普通历史的可信指令 | 范围级 CRUD、优先级、触发条件；仅管理入口可写 | PostgreSQL 隔离及 instruction-shaped memory 测试 |
| 管理与诊断 | 任务状态、管理界面和追踪 | operation 跨 retain/consolidation/model 阶段关联；API/CLI/MCP 重试取消；事实、历史、实体纠正、策略/模型/指令页面 | BFF/API 测试、真实 operation/observation 场景、前端 build/test |

## 固定对照结果

最终报告位于 `benchmark/memory-parity/runs/b7-full-20260909-2/report.json`。44 个
固定案例形成 88 个引擎行；其中 23 个案例、46 行使用相同模型的真实上游/TKB
输出，剩余案例在 TKB 使用真实数据库、独立进程或生产契约门禁，在上游侧使用
固定提交的源码契约作行为参照。所有 11 个类别的 TKB 正确率为 100%，确定性门禁
全部通过。

真实模型部分没有失败。上游 p50/p95 为 9.000/34.812 秒、41,357 tokens；TKB
p50/p95 为 14.891/52.325 秒、60,569 tokens。该延迟和用量只覆盖 23 个抽取/归纳
案例，不能外推为线上吞吐量。其余契约案例没有伪造模型用量或延迟。

## 明确排除与后续兼容范围

下列内容不属于本次核心能力对齐的完成条件，后续应作为独立变更评估：

- Hindsight 全部 SDK 的线协议、客户端类型和异常逐项兼容。
- Hindsight 的全部第三方框架、托管服务和外部产品集成。
- 每一种可选文本、向量或混合搜索后端及其全部调参面。
- 对上游未来提交的自动持续兼容；本次基线固定为 `9676fc169`。

这些排除项不影响 TKB 当前 MCP/BFF/Pi 接口或本次九份规格。新后端或 SDK 兼容需
沿用可信 scope、来源 tombstone、引用复核和任务 fencing，不能绕过现有生命周期。
