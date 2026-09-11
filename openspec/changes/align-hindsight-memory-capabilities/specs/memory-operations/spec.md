## Purpose

为 TKB 提供可验证的 memory-operations 能力，使长期记忆在多次写入、检索、更新与管理过程中保持明确的行为契约，并支持与本地 Hindsight 基线进行分批对照验收。

## ADDED Requirements

### Requirement: Traceable recoverable operations
系统 SHALL 为交付、抽取、归纳、刷新提供关联操作标识、阶段状态、重试次数、错误、耗时和预算使用；提供列表、详情、受控重试及取消入口，诊断默认不泄露原文。

#### Scenario: 追查未记住的轮次
- **WHEN** 管理员按 session/turn 查询
- **THEN** 可定位最后成功阶段、失败原因与可恢复操作

### Requirement: Memory management
系统 SHALL 提供范围配置、事实及来源查看、observation 历史、模型与指令管理入口，读取和修改遵守同一范围约束。

#### Scenario: 解释旧认识
- **WHEN** 管理员查看一条旧 observation
- **THEN** 可查看来源、历史、新鲜度和刷新任务，无权范围不可见

### Requirement: Reproducible parity acceptance
系统 SHALL 提供固定输入和查询的上游/TKB 对照验收，记录代码版本、模型、配置、数据版本、正确性指标、延迟与用量；每批结果 SHALL 独立记录，未验证项不得标为对齐。

#### Scenario: 验收一批
- **WHEN** 完成一批实现并运行对应案例
- **THEN** 产出可复核结果，范围泄漏、丢失交付和删除复活等确定性检查全部通过

