## Purpose

为 TKB 提供可验证的 mental-models 能力，使长期记忆在多次写入、检索、更新与管理过程中保持明确的行为契约，并支持与本地 Hindsight 基线进行分批对照验收。

## ADDED Requirements

### Requirement: Model definition and refresh
系统 SHALL 支持模型创建、读取、更新、删除，保存问题定义、范围、来源与刷新配置；支持手动、相关事实触发及定时刷新，并可查看失败状态。

#### Scenario: 项目概况刷新
- **WHEN** 已定义项目概况且相关事实更新
- **THEN** 触发刷新后概况更新；无关范围变化不触发该模型刷新

### Requirement: Versioned bounded refresh
系统 SHALL 保存模型版本和最后成功刷新时间，支持完整刷新及结构化增量更新；失败 SHALL 保留最后成功版本并标示新鲜度，预算与重试必须有界。

#### Scenario: 刷新失败与删除竞争
- **WHEN** 模型刷新失败或生成期间来源被删除
- **THEN** 旧成功版本不会被失败结果覆盖，包含删除来源的内容不会发布

