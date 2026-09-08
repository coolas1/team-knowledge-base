## 1. Execution backend contracts

- [x] 1.1 定义 job 请求、能力声明、结果/错误、取消和健康协议，加入有界 schema 与认证；通过契约测试验证非法镜像/挂载/管理参数不能进入 job 模板，错误与能力缺口可区分。
- [x] 1.2 新建 `src/extensions/tool-runner/` 和 JavaScript 任务镜像，固定镜像/依赖版本及 ESM 输入输出约定；通过真实镜像测试验证程序执行、Date/Intl、临时文件、HTML 模块和 JSON 结果。
- [x] 1.3 实现每 job 容器模板、资源上限及临时目录清理；容器集成测试确认无产品/会话/密钥/socket 挂载、非 root、只读文件系统、无网络及各 job 状态隔离。
- [x] 1.4 实现超时、取消、进程终止、输出限额及 runner 重启清理；以死循环、过量内存/日志、派生进程和中断测试确认及时结束且只清理自身 jobs。

## 2. Brokered capabilities

- [x] 2.1 实现任务 stdin/stdout RPC 和 `host.fetch`，限制消息大小、关联 ID 和每 job 请求次数；协议测试验证伪造/损坏消息不会越权并产生可诊断失败。
- [x] 2.2 实现公共 HTTP(S) GET/HEAD broker，验证 DNS/实际连接/每跳重定向和响应限额；集成测试覆盖 IPv4/IPv6/映射地址、私网重定向、DNS 变化、超时、取消和解码后大响应。
- [x] 2.3 实现命名 capability profiles 与服务端秘密引用；模拟认证 API 验证固定 origin/路径/方法约束、未配置能力失败及 secret 不进入 job 输入、输出和事件。

## 3. Persistent tool library

- [x] 3.1 实现工具 manifest、SQLite 元数据和内容寻址代码存储；测试 schema/名称/权限校验、原子写入、重启恢复及损坏/缺失 blob 不可执行。
- [x] 3.2 实现 publish 的服务端测试流程，支持 JSON 期望、容差和预期失败；用通过/失败/修改后代码/伪造报告用例验证只有当前确切版本通过真实测试才能激活，测试也在隔离 job 中执行。
- [x] 3.3 实现版本 CAS、固定版本调用、停用与权限复查；测试并发发布冲突、旧版本不变、停用拒绝、执行中版本稳定和错误版本不替换当前版本。
- [x] 3.4 实现有界元数据检索及按 ID 获取 schema/代码/测试摘要；测试新会话与进程重启后的发现复用、空结果、未知 ID 和 gen_ 命名空间不覆盖内置工具。
- [x] 3.5 加入工具参数化说明、共享数据边界及明显秘密模式校验；测试已知凭据模式拒绝与日志清理，并在文档明确静态扫描不能证明实现完全无私有信息。

## 4. Agent runtime integration

- [x] 4.1 实现 execute_code、find_tools、publish_tool、call_tool 并接入现有 allowlist；SDK 会话测试验证新增业务名称无需加入固定工具数组即可在同轮发布后调用。
- [x] 4.2 将工具调用、修复构建链、发布测试和 job 执行纳入统一限额及取消；测试换 buildId 不绕过整体预算、发布多个测试消耗预算、轮次重置及取消传播到 runner。
- [x] 4.3 修复 MCP/read/bootstrap 错误传播并保留预算终止；真实 Pi 执行循环测试验证工具历史和 SSE isError 一致，成功空结果不变，超限不会无限重试。
- [x] 4.4 增加执行环境和工具库健康状态及配置，保留 TKB 在 runner 不可用时的行为；测试健康检查不启动 job/模型/付费 API，不暴露 token。
- [x] 4.5 添加自主工具构建技能和系统指引，说明先复用再创建、真实执行反馈、参数化保存和外部权限缺口；模拟 prompt 验证实际加载指引与 runner 能力，既有记忆上下文和保留测试继续通过。

## 5. User-visible activity

- [x] 5.1 为 SSE 添加可选 activity/jobId/tool version/errorSummary，摘要化 bootstrap tool.start.args；测试默认不泄露源代码、完整输入/输出或秘密，并保持旧事件与 BFF 透传兼容。
- [x] 5.2 在前端展示执行、测试、修复、保存、复用和失败记录；组件或状态测试验证失败后成功仍保留过程、重复事件去重、新轮次/会话隔离及未知字段兼容。

## 6. Deployment and independent acceptance

- [x] 6.1 增加 runner、任务镜像和工具库卷的部署配置及 README，保留用户已有 Compose 修改；核对管理访问仅限 gateway、任务固定模板和回滚步骤，配置检查不打印秘密。
- [x] 6.2 建立独立验收夹具：空工具库、日期/时区、未预置 JSON 转换、计算、公开页面提取和人为失败修复；交付独立预期输出及实际代码/执行/版本证据判定，不能以模型自测代替验收。
- [x] 6.3 运行真实模型自主构建 smoke 和跨会话复用 smoke，验证无需开发者加业务工具、无需重启即可执行新能力；记录真实调用链，缺模型/runner/网络条件的项注明未验证，不计为通过。
- [x] 6.4 运行 Pi 与 runner 的类型检查、测试、构建/依赖安全检查，SPA 测试和构建，仓库 `uv run ruff check` 与 `uv run pytest`；记录结果并处理本变更引入的问题。
- [x] 6.5 对照规范完成场景覆盖和隔离/取消集成验证，运行 `openspec validate enable-agent-tool-authoring --strict`；交付变更范围、验证记录和限制，明确旧 `fix-pi-agent-runtime-tools` 不作为实施依据。
