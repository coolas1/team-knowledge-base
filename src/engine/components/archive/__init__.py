"""自动归档组件：扫描 inbox -> 分类 -> 校验 -> 执行 -> 日志/撤销。

模块划分见 add-auto-file-archiving design.md D1:
- scanner: 轮询 inbox（稳定性 + 临时文件过滤 + sha256 去重）
- jobs:    Postgres 持久队列（claim/lease/retry，仿 graph_outbox）
- classifier: 目录画像 + 候选检索 + LLM 结构化决策
- planner:  ActionPlan 构造 + 安全校验
- executor: move + 操作日志 + 触发知识库入库
- journal:  撤销（hash 校验 -> 反向 move -> 保留知识并更新路径）
- worker:   单个 job 的处理编排
- runtime:  scanner+worker 的生命周期封装（BFF lifespan 内启停）
"""
