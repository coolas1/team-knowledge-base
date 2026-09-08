## Why

TKB 已有 retain/recall/reflect 基础，但跨次 observation 更新、长期摘要维护和端到端可靠交付尚未闭环，重复与过期认识会影响连续对话。以本地 Hindsight `9676fc169` 为行为参照、TKB `f33759a2` 为调查基线，分批补齐核心记忆能力，每批有独立验收和回退路径。

## What Changes

- 分七批实施：范围与基线、可靠保留、持续归纳、检索与证据、Mental models、动态 Reflect、管理与综合验收。
- 新增 bank/scope 上下文及标签过滤；旧调用继续使用共享团队默认范围，不新增必填身份字段。
- 建立可补投的会话交付、明确的抽取降级状态、说话者身份和事件参考时间。
- 增加后台 consolidation、精确/语义去重、版本历史、实体解析、增量 retain 和删除传播。
- 增加模型定义与刷新、动态证据工具循环、请求级预算、策略与诊断管理。
- 保留现有 Postgres/Neo4j、Pi/MCP/BFF 接口，按功能开关渐进启用。
- 本提案对齐核心服务行为及 TKB 管理入口；不承诺 Hindsight 全部 SDK 的线协议兼容、全部第三方集成或每一种可选搜索后端。这些单独列为后续兼容范围，不作为本次完成条件。

## Capabilities

### New Capabilities
- `memory-scope`: 范围、身份、标签和配置隔离。
- `memory-retention`: 抽取质量、实体解析、时间语义及增量写入。
- `memory-consolidation`: 持续归纳、去重、历史、删除传播及恢复。
- `memory-retrieval`: 请求级过滤、预算、原文展开及证据新鲜度。
- `mental-models`: 定义、刷新、版本及来源生命周期。
- `memory-reflection`: 动态工具循环、引用和指令策略。
- `memory-operations`: 操作状态、诊断、管理和对照验收。

### Modified Capabilities
- `conversation-memory`: 默认共享范围兼容、持久化补投、不可信证据注入及跨来源遗忘语义。
- `ingest`: 文档归属、原文访问、修改/删除、普通 GraphRAG 检索统一使用可信范围，旧数据保持 default-team。

## Impact

- `src/engine/hindsight_components/` 的 types/protocols/repository/models/retain/recall/reflect、conversation queue/worker、graph outbox；新增相应服务与迁移。
- `src/engine/interface.py`、引擎配置及 MCP/BFF 边界；Pi runtime、conversation-memory 和持久化会话层。
- 经用户再次批准，B1 增加 Pi 会话创建、列表、历史、取消、删除及遗忘权限，和每会话 MCP 隔离；旧会话保持 default-team。
- 经用户批准，B1 扩展到共享 documents/chunks 存储、GraphRAG backend/search/pipeline 及文档原文接口，防止通过非记忆入口绕过范围。
- 前端记忆诊断和管理入口、引擎/契约/前端测试及离线对照评测数据。
- 需要 additive 数据迁移和可续跑回填；归纳、实体解析和刷新增加 LLM 调用，须配置并发、预算和重试上限。
- 本次仅创建计划文档；实施、部署和实际效果对齐须后续逐批完成并验收。
