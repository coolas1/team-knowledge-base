## Context

动机与替代关系见 proposal，行为契约见 `specs/agent-tool-authoring/spec.md`。本变更引入独立执行后端和持久工具生命周期，需明确设计。

当前 `runtime.ts` 在 createAgentSession 时传入静态 `tools` 名称数组。已安装 Pi 0.83.0 的 `_refreshToolRegistry()` 会把不在该 allowlist 的动态工具过滤掉。SDK 本身支持运行中 `pi.registerTool()`，但仅增加注册调用无法穿透项目现有策略。参考本地 `docs/extensions.md:1337` 和 `dist/core/agent-session.js`，外部参考为 [Pi 扩展文档](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/extensions.md)。

当前 Node 容器持有模型凭据、会话文件和应用代码，不能作为生成程序的运行边界。已有会话记忆是单团队共享模式；工具库沿用部署级团队范围，不在本变更增加用户身份体系。

## Goals / Non-Goals

**Goals:**

- 产品只维护通用执行和管理机制，具体日期、计算、网页解析等实现由模型按需编写。
- Agent 在部署授权范围内自主试错和复用，用户无需为每个生成工具走审批流程。
- 程序、测试、版本与真实执行记录关联；失败可修复，取消与资源预算可强制执行。

**Non-Goals:**

- 修改 Agent 模型权重、自动修改产品源码、向主进程热加载生成扩展。
- 首版开放任意系统软件安装、多语言运行环境或自动获得外部服务账号。
- 将模型生成的测试通过等同于功能完全正确或执行代码可信。
- 默认把生成工具展开为大量 Pi 原生 function schemas；持久库的发现和调用先通过通用入口完成。

## Decisions

### 1. Four bootstrap tools, extensible implementations

保留 TKB 工具和受限技能读取，新增四个基础入口：

| Entry | Contract |
| --- | --- |
| execute_code | 提交 JavaScript 程序、JSON 输入、能力声明和可选构建链 ID，返回执行结果/错误 |
| find_tools | 按关键词与用途检索已发布工具，按 ID 查看具体版本的 schema、实现和测试摘要 |
| publish_tool | `action=publish` 提交工具与测试并验证保存；`action=retire` 停用版本 |
| call_tool | 按工具 ID、确切版本和参数执行已保存工具 |

这些入口构成固定的可信外层；业务实现是工具库中的数据和程序，不是开发者预置路由。例如 Agent 可保存 `gen_timezone_clock` 或 `gen_json_grouping`，通过 `call_tool` 立即调用。无需改静态 allowlist、重启或把源代码 import 到主服务。

替代方案：Pi 原生动态注册可提供更直接的模型 schema，但需改白名单、处理会话作用域/命名冲突及上下文膨胀；首版通过通用调用已满足动态创建和执行，后续可增加由可信包装器代理的按需注册。包装器必须远程执行代码，不能直接执行生成的 extension factory。

### 2. Agent owns the authoring loop

在打包技能中新增工具构建指南，并在系统提示词说明当前 runner 能力。Agent 先选择现有 TKB/已保存工具；缺口适合编程时自行形成代码和参数，调用 execute_code 观察真实输出。失败后读取诊断并修复，成功后回答用户；有复用价值的实现补充边界用例并提交 publish_tool。一次性程序可不保存。

运行语言首版为 JavaScript ESM，约定 `export default async function(input, host)`，返回 JSON。标准库、Date/Intl、临时文件足够覆盖时间、常规计算与结构化数据处理；`host.fetch()` 提供经 broker 校验的网络读取，`host.request()` 仅访问管理员预配置能力。时区通过非秘密运行配置提供默认 `Asia/Shanghai`，代码可接受显式时区参数，不硬编码当前日期。任务镜像提供固定版本的 HTML 解析模块，Agent 编写实际抓取/提取逻辑。

不使用按问题关键词路由到六个写死实现，也不专门增加“日期工具不存在就回复不能查询”的分支。构建循环仍由主 Agent 驱动，不添加不受整体预算约束的第二个自旋 LLM 循环。

### 3. Isolated job containers behind a runner gateway

新增 `src/extensions/tool-runner/` 服务与 Node 任务镜像。Pi 通过私有 API 提交 jobs，runner 从服务端固定模板启动短生命周期任务容器，返回结构化事件和结果。管理接口仅接受受限 job 数据，不接受容器镜像、挂载路径、Docker 参数或命令行模板。

任务容器固定镜像 digest、非 root、只读 rootfs、专用有界 tmpfs 工作目录、去除 capabilities、no-new-privileges、默认 seccomp、无宿主挂载和无网络。默认每个 job 1 CPU、256 MiB 内存、32 个进程、32 MiB tmpfs、20 秒墙钟；输入/代码上限分别 256/64 KiB，结果与日志合计上限 256 KiB。控制程序经 stdin 送入源代码及输入，任务入口在临时目录运行它，生成代码的 import 仅发生在任务容器。

runner gateway 是可信基础设施，Docker 管理访问只授予它；优先使用专用 rootless Docker daemon。Pi、浏览器和任务容器都不持有 Docker socket。部署不具备此后端时明确报告不可执行，不能静默退回主容器 eval/child_process。gateway 使用服务端认证和私有网络，凭据绝不注入 job。

Pi 不把完整 process.env 传入作业，不挂载会话/工具库卷，只发送当前任务必要的参数和程序。每个测试同样启动受限作业；超时、取消、客户端断开与 runner 恢复清理都终止整项容器。作业加服务端标签，runner 重启时清理遗留且属于自身的 jobs，不能清理其他容器。

替代方案：Node vm 和同进程动态 import 不构成隔离；[Node 官方文档](https://nodejs.org/api/vm.html)明确指出 vm 不是安全机制。单个长期共享执行容器会让前一段生成代码影响后续作业。任务容器仍共享宿主内核，因此首版面向当前可信团队部署，不声称提供对恶意租户的强多租户隔离。

### 4. General network capability, no hard-coded search vendor

任务容器 `network=none`，网络操作通过 stdin/stdout 的有界关联 ID 协议请求 gateway broker；host.fetch 是该协议的客户端。任意伪造协议请求仍需通过相同 broker 校验，生成代码不具备直接联网能力。stderr 作为有限日志，协议损坏导致明确执行失败。

部署默认允许公共 HTTP(S) GET/HEAD，broker 校验 URL、每次 DNS 结果与实际连接地址、每个重定向，拒绝非公网地址及内嵌凭据。单次响应解码后上限 2 MiB、最多三跳、每 job 最多十次请求，并受 job 总时限约束。请求之间无共享 Cookie；检索页面中的指令不改变执行策略。

需要认证的服务采用管理员提供的 capability profile：命名能力、固定 origin/路径/方法约束、secret reference 和超时。程序只能使用能力别名和经过验证的非秘密参数，broker 注入凭据且不将凭据回传。写入式 API 不在默认公共能力中；若部署显式授权某 profile，代码才能调用它。缺 profile、包或服务权限时返回具体依赖缺口。

搜索工具可由 Agent 针对可用公开端点或已配置搜索能力编写，不指定 Brave 为必需依赖，也不保证任意搜索网站都允许匿名访问。外部来源暂时不可达时保留失败证据，不把抓取脚本创建成功当作搜索成功。内部文档/记忆不会自动发送至公网；提示词要求仅传任务必要的非敏感参数，部署可进一步收紧域名 allowlist。

### 5. Persistent versioned tool library

新增 `tool-library.ts`，工具库归 Pi 可信管理代码所有，使用独立持久卷中的 SQLite 元数据事务与按 SHA-256 内容寻址的源文件。schema、描述、测试、运行时镜像标识、依赖、权限和代码共同形成版本哈希。工具名限制 `gen_` 命名空间，不能覆盖 TKB/read/bootstrap 工具。

find_tools 首版使用元数据全文/关键词检索（不引入向量数据库依赖）；按 ID 可获取代码供修复，结果有界。工具库按当前单团队部署共享，工具参数化；不得将用户原始文档、当前日期常量、会话记录或凭据固化为共享实现/测试。Agent 指引和明显秘密模式校验降低误存，不能宣称静态扫描能证明不存在所有私有数据。

publish_tool 校验有界 JSON schema（不解析远程 $ref）、源码大小、声明的运行时与能力是否已授权、至少一个成功样例及一个边界/失败样例。服务端使用提交的确切版本运行测试，输入/输出以 schema 和测试期望校验。测试期望支持 JSON 相等、数值容差或声明应失败，不能让模型提交任意测试判定代码在可信进程运行。日期这类外部变化值可用标准库纯转换测试和独立运行时 smoke 验证，避免用易过期的当前时间常量。

版本状态为 draft/validated/active/retired；失败发布保留有界诊断，不激活。代码 blob 先写临时文件再原子重命名，SQLite 事务记录通过报告与 active 指针，compare-and-swap previousVersion 防止并发覆盖。崩溃可能留下无引用 blob，可由定期清理处理，不能出现 active 指向缺失代码。call_tool 固定版本后执行，校验输入/输出，复查当前授权；使用受控回退或新修复版本，绝不静默修改历史版本。retire 阻止新调用，不强杀已开始的合法调用。

替代方案：只写 SKILL.md 不提供真实执行；只保存无版本脚本难以复现；自动修改 `tools.ts` 或加载 JS 扩展会破坏主服务边界。

### 6. Shared budgets and truthful errors

现有 executeMcpTool 与 read 的失败改为 SDK 认可的抛出异常，bootstrap 工具也如此。保留限额终止机制，回归测试走真实 Pi 执行循环，不能只检查 `execute()` 返回对象。

每轮仍使用整体 maxToolCalls/maxRunSeconds，并新增最大 job 数（默认 12）和每条构建链最大修复尝试（默认 3）。execute_code 的可选 buildId 由服务器签发并与当前会话/轮次绑定；新 buildId 仍消耗全局 job 预算，防止换名绕过限制。publish_tool 内的每个测试单独消耗 job 预算，call_tool 每次执行也消耗预算，所有剩余时限向 runner 传播。工具定义不能声明更高限额或改变网络策略。

SSE 保持现有 tool.start/tool.result，增加可选 activity、jobId、artifactId/version 和已清理的 errorSummary。前端展示执行/测试/修复/保存/复用过程，失败记录在本轮完成后保留。原始代码和参数默认不发往前端，因此 bootstrap 的 tool.start.args 需专门摘要化，不能沿用当前无条件转发全部 args 的逻辑。

### 7. Availability and rollout

健康信息增加 runner reachable、runtime image/version、有效资源/网络能力和 library 状态。可选 runner 失效不令既有 TKB 路由整体不可用；涉及自主工具的入口准确报告 runner_unavailable。能力提示让模型区分“没有合适业务工具”和“没有执行后端”。健康检查不启动任务、不调用模型或外部付费服务。

主要配置：`PI_AGENT_TOOL_AUTHORING_ENABLED=true`、`PI_AGENT_RUNNER_URL`、服务端 runner token、`PI_AGENT_TOOL_LIBRARY_DIR`、`PI_AGENT_MAX_CODE_JOBS=12`、`PI_AGENT_MAX_BUILD_ATTEMPTS=3`、`PI_AGENT_TIMEZONE=Asia/Shanghai`；runner 单独配置固定镜像、资源上限、允许公网读取和 capability profiles。无 runner URL 的直接启动模式保留 TKB 并报告未配置。具体镜像 digest 和平台适配在实施时固定，必须通过部署隔离测试。

## Risks / Trade-offs

- [生成实现和自测可能一起出错] → 测试通过仅是激活条件；验收使用独立预期和未知输入，运行失败可退役并修复版本。
- [授权代码仍可滥用资源/尝试外传] → 每 job 隔离、broker 控制、严格全局预算；生成程序永不直接持有主服务秘密，执行数据最小化。
- [容器启动开销] → 首版优先可验证隔离，缓存固定镜像；测量任务延迟后再考虑不共享可写状态的预热池。
- [持久化坏工具影响后续会话] → 不可变版本、测试记录、复查权限、显式退役和独立验收。
- [模型持续写错或拒绝使用执行入口] → 收敛重试并给出失败事实，真实模型 smoke 不通过时不能宣称功能完成。
- [新增执行服务增加部署复杂度] → 提供 Compose 配置和健康诊断；保持原 TKB 接口可独立运行，不用不隔离的后备路径隐藏部署问题。

## Migration Plan

1. 仅实施本变更，旧固定业务工具提案不执行。新增可信 runner、任务镜像和通用工具机制，不预置验收业务实现。
2. 本地使用模拟 gateway 验证编排、库版本和错误；容器集成验证隔离、限额、取消和受控网络。
3. 部署 runner 与独立工具库卷，保留用户现有 Compose 修改。配置运行时/网络授权后重建 Pi 与包含 SPA 的 Webapp；无数据库业务迁移。
4. 用空工具库和真实模型验证日期、未预置数据转换、失败修复、公开页面提取和跨会话复用。缺真实依赖的用例记录未验证，不计为通过。
5. 回滚关闭自主工具开关并停止新 job，保留库卷供恢复；旧 TKB 会话接口继续工作，任务镜像变更后的旧工具版本必须检查运行时兼容性后才能使用。
