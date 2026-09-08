## Purpose

为 TKB 提供可验证的 memory-retention 能力，使长期记忆在多次写入、检索、更新与管理过程中保持明确的行为契约，并支持与本地 Hindsight 基线进行分批对照验收。

## ADDED Requirements

### Requirement: Speaker and temporal grounding
系统 SHALL 区分用户 world 事实和 Agent experience，保留说话者、来源时间与入库时间，依据来源参考时间解析相对日期；未知身份 MUST 保留未知状态。

#### Scenario: 补录与建议
- **WHEN** 补录旧聊天包含用户昨天完成的事项和 Agent 建议执行的事项
- **THEN** 事件时间以旧聊天为基准，建议不被提取成已完成行动

### Requirement: Explicit extraction outcomes
系统 SHALL 区分正常空结果、成功抽取、降级原文和失败；降级或失败 SHALL 可诊断和重试，不能冒充完整成功。

#### Scenario: 抽取服务失败
- **WHEN** 抽取服务超时而原文已保存
- **THEN** 操作报告抽取未完成并能恢复处理

### Requirement: Entity resolution
系统 SHALL 在范围内支持别名解析、上下文消歧和误合并纠正，并保留原始名称来源。

#### Scenario: 同名冲突
- **WHEN** 两个不同人的姓名相同但身份上下文冲突
- **THEN** 两者不被自动合并；已发生误合并可以纠正

### Requirement: Incremental document retention
系统 SHALL 支持明确的 append/replace 写入语义、重复请求幂等、未变化事实复用及并发版本冲突处理。

#### Scenario: 追加及重试
- **WHEN** 同一文档追加内容后重试相同请求
- **THEN** 追加内容只生效一次，未变化内容无需重复抽取，来源引用仍有效

