## Purpose

为 TKB 提供可验证的 memory-consolidation 能力，使长期记忆在多次写入、检索、更新与管理过程中保持明确的行为契约，并支持与本地 Hindsight 基线进行分批对照验收。

## ADDED Requirements

### Requirement: Persistent scoped consolidation
系统 SHALL 在事实持久化后持续处理待归纳事实，结合范围内历史 observation 执行创建、更新和删除；任务失败 SHALL 可续跑且不重复应用已提交动作。

#### Scenario: 跨轮及重启
- **WHEN** 三轮相关事实分次进入且归纳中途重启
- **THEN** 恢复后形成引用三轮有效证据的综合认识

### Requirement: Observation deduplication
系统 SHALL 对创建和更新执行精确去重，并提供可配置语义去重；合并 SHALL 保留有效证据且不跨范围。

#### Scenario: 重复偏好
- **WHEN** 同一偏好被反复改写表达并同时入库
- **THEN** 最终不产生等价 observation 持续膨胀，证据来源可查询

### Requirement: History and freshness
系统 SHALL 保存 observation 修改前版本、证据变化和时间，区分变化与未解决冲突，并暴露过期状态。

#### Scenario: 偏好发生变化
- **WHEN** 新事实明确取代旧偏好
- **THEN** 当前认识反映变化，旧版本可查，矛盾证据不被静默掩盖

### Requirement: Source removal propagation
系统 SHALL 在来源删除或替换后立即使受影响派生内容停止作为有效证据返回，并清理或重算其有效证据；并发任务 MUST NOT 重新引入已删除事实。

#### Scenario: 删除部分来源
- **WHEN** 跨来源 observation 的一个来源被遗忘
- **THEN** 被删来源独有信息不可召回，其余来源事实保留，派生内容重算或失效

