## Purpose

为 TKB 提供可验证的 memory-scope 能力，使长期记忆在多次写入、检索、更新与管理过程中保持明确的行为契约，并支持与本地 Hindsight 基线进行分批对照验收。

## ADDED Requirements

### Requirement: Scope propagation
系统 SHALL 为记忆、实体、关系、任务和派生内容应用一致的 bank 与可见范围；客户端请求的范围 MUST 经过服务端允许范围校验。

#### Scenario: 跨范围读取
- **WHEN** 请求范围 A 的召回、图扩展或原文展开
- **THEN** 结果不包含范围 B 的内容或引用，即使二者实体同名

### Requirement: Tag matching and observation scopes
系统 SHALL 支持 any/all/any_strict/all_strict/exact 与组合标签过滤；非严格模式包含无标签内容，严格模式排除无标签内容，exact 空标签仅选择无标签内容。归纳写入范围 SHALL 独立于会话来源标签并由服务端配置。

#### Scenario: 跨会话归纳
- **WHEN** 同一用户两个会话拥有相同用户归纳范围
- **THEN** 可以更新同一用户认识，但不混入其他用户事实

### Requirement: Scoped configuration
系统 SHALL 管理范围级 Agent 名称、retain mission、抽取模式和命名策略、Reflect mission 及预算配置，并保留生效版本。

#### Scenario: 策略变更
- **WHEN** 管理员修改某范围抽取策略
- **THEN** 后续操作使用新版本且其他范围不受影响

