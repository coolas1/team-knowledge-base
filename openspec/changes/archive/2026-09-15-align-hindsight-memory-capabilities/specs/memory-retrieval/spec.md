## Purpose

为 TKB 提供可验证的 memory-retrieval 能力，使长期记忆在多次写入、检索、更新与管理过程中保持明确的行为契约，并支持与本地 Hindsight 基线进行分批对照验收。

## ADDED Requirements

### Requirement: Configurable retrieval contract
系统 SHALL 支持类型、标签范围、查询参考时间、最低分、observation 偏好、请求预算及可选原文/来源事实/实体输出；各检索分支 SHALL 执行相同过滤且受服务端上限约束。

#### Scenario: 过滤贯穿
- **WHEN** 调用方仅请求某范围 world 事实并指定预算
- **THEN** 语义、关键词、图和时间分支均遵守过滤，总输出与处理期限不超配置上限

### Requirement: Evidence expansion and freshness
系统 SHALL 支持按记忆引用展开 chunk/document，返回证据来源与新鲜度；缺失、删除和越权引用 SHALL 不返回源内容。

#### Scenario: 过期综合认识
- **WHEN** 召回 observation 的来源已发生变化
- **THEN** 结果标识过期并允许在原范围内查证当前事实

