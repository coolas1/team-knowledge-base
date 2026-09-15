## Purpose

为 TKB 提供可验证的 memory-reflection 能力，使长期记忆在多次写入、检索、更新与管理过程中保持明确的行为契约，并支持与本地 Hindsight 基线进行分批对照验收。

## ADDED Requirements

### Requirement: Adaptive evidence loop
系统 SHALL 支持循环选择模型搜索、observation 搜索、事实召回和原文展开，根据新证据继续规划或结束；过期综合认识 SHALL 回查事实，迭代次数、工具输出和总期限必须有界。

#### Scenario: 追加发现缺口
- **WHEN** 第二次工具结果暴露新的关键证据缺口
- **THEN** 剩余预算允许时继续针对性检索，预算耗尽时明确证据不足

### Requirement: Validated citations and directives
系统 SHALL 分别记录检索证据与实际引用，对最终引用执行存在性及范围校验；管理员 directives SHALL 与不可信历史内容分离并按范围及触发规则应用。

#### Scenario: 伪造引用或历史指令
- **WHEN** 模型输出未知引用或历史记忆要求改写系统规则
- **THEN** 无效引用不作为有效依据返回，历史指令不覆盖受信任策略

