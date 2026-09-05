## Purpose

赋予知识库 Agent 在任务中自行弥补工具缺口的能力：发现现有能力不足后编写可执行实现、根据真实执行结果修复，并将验证过的参数化工具持久保存供后续调用，而无需开发者逐项内置业务工具。

## ADDED Requirements

### Requirement: Autonomous capability construction

Agent SHALL 优先检查已注册工具和可复用工具库；没有合适实现且运行环境允许时，SHALL 主动编写代码并实际执行，利用错误反馈进行有限修复。在已配置的执行和网络权限内，生成、试运行、修复和保存工具 SHALL 不要求用户逐个确认。生成代码文本本身 SHALL 不被当成任务完成。

#### Scenario: Missing date tool
- **WHEN** 全新的工具库没有日期工具，用户询问某时区当前日期
- **THEN** Agent 使用通用执行能力编写并运行读取系统时钟的代码，根据实际结果回答，不要求开发者新增日期接口

#### Scenario: Previously unanticipated transformation
- **WHEN** 用户提出预置业务工具没有覆盖的文本/JSON 转换或计算任务
- **THEN** Agent 能生成对应程序、运行并给出结果，不依赖按任务名称预写的路由或实现

### Requirement: General execution contract

系统 SHALL 提供 `execute_code`，接受 JavaScript 源代码、JSON 输入和执行能力声明，返回服务端生成的执行标识、执行状态、JSON 结果、有界日志及可用于修复的错误。程序 SHALL 能访问运行环境的标准库、系统时钟、自己的临时工作目录，以及部署允许的公网请求能力。

#### Scenario: Real execution failure
- **WHEN** 生成程序出现语法错误、运行异常或输出不符合 JSON 契约
- **THEN** 返回真实失败及有界错误信息，Agent 可以基于这些信息重新提交修正后的程序

#### Scenario: One-off computation
- **WHEN** 用户只需要一次计算或数据转换
- **THEN** Agent 可以执行并返回结果，不必先保存为永久工具

### Requirement: Parameterized tool publication

系统 SHALL 提供 `publish_tool`，接受名称、描述、输入/输出 JSON schema、代码、运行时要求、权限声明和测试用例。服务端 SHALL 对即将保存的确切代码和元数据版本重新验证并执行测试，只有通过的版本才能变为可调用。模型声称测试成功或提交任意历史执行标识 SHALL 不足以发布。

#### Scenario: Publish a reusable implementation
- **WHEN** Agent 为重复需求生成参数化实现，接口校验和实际测试均通过
- **THEN** 系统原子保存不可变版本、代码哈希和服务端测试记录，返回可调用的工具 ID 与版本

#### Scenario: Untested or changed code
- **WHEN** 代码在一次成功试运行后被修改，或模型声明通过但实际测试失败
- **THEN** 修改后的版本必须重新测试；失败版本不得替换已有可用版本

### Requirement: Discovery and reuse across sessions

系统 SHALL 提供 `find_tools` 和 `call_tool`，让 Agent 检索当前团队工具库的名称、用途、schema、版本和能力需求，并使用 JSON 参数调用固定版本。调用 SHALL 验证输入和输出契约，实际执行已保存代码，并保留与临时程序相同的执行边界。

#### Scenario: Reuse after restart
- **WHEN** 某工具已发布，Pi 服务重启后另一会话遇到相同需求
- **THEN** Agent 能检索并调用已保存版本，不需要重新生成或开发者重新部署

#### Scenario: No business tool in static Pi list
- **WHEN** 工具库新增一个从未出现在 Pi 固定工具列表中的名称
- **THEN** 该工具可在当前会话通过通用调用入口立即使用，不需要修改 allowlist 或重启进程

### Requirement: Immutable versions and retirement

工具库 SHALL 保持已发布版本不可变；修复 SHALL 创建新版本，并仅在验证通过后切换当前版本。系统 SHALL 支持通过 `publish_tool` 的停用操作禁止后续调用指定版本。正在执行的调用 SHALL 固定在已选择版本，不受并发发布影响。生成工具 SHALL 不得覆盖 TKB 或通用入口。

#### Scenario: Concurrent repair
- **WHEN** 两个会话同时基于同一当前版本发布修复
- **THEN** 系统避免静默覆盖，至少一个请求获得明确的版本冲突；已开始的执行继续使用原有内容

#### Scenario: Retired version
- **WHEN** 某版本被停用后再次调用该版本
- **THEN** 调用明确失败且不会执行它，检索结果不把它呈现为可用

### Requirement: Execution isolation

生成代码及其测试 SHALL 在独立任务环境执行，不在 Pi 服务或工具库管理进程内直接加载。程序 SHALL 不可访问产品源码、主服务环境变量和凭据、其他任务文件、会话存储、工具库写权限或宿主容器管理接口。每项任务结束、失败或取消后 SHALL 清理其临时状态。

#### Scenario: Attempts to access host state
- **WHEN** 程序尝试读取主服务密钥、其他会话文件或容器管理接口
- **THEN** 无法获得这些资源，当前执行失败或返回访问错误，其他会话与服务状态不受修改

### Requirement: Controlled external capabilities

公网 HTTP(S) 请求 SHALL 受部署网络策略、超时、重定向和大小限制约束，并拒绝访问本机、私有、链路本地或其他非公网地址，包含 DNS 与重定向绕过。已有外部凭据 SHALL 通过受限服务端能力引用使用，不进入生成代码。无凭据或无访问权限的服务 SHALL 明确报告不可用；Agent SHALL 不把自行创建调用代码描述为已取得服务权限。

#### Scenario: Create a page extraction tool
- **WHEN** 公网读取已授权且用户提供公开页面 URL
- **THEN** Agent 能生成抓取和解析逻辑并调用授权网络能力，实际获取内容后回答并引用来源

#### Scenario: Search provider credential unavailable
- **WHEN** 任务依赖的搜索服务要求密钥且部署没有该凭据
- **THEN** Agent 明确说明缺少依赖，可使用其他已授权且真实可用的来源，不能伪造搜索结果或绕过访问限制

### Requirement: Bounded authoring loop

创建、试运行、测试和调用 SHALL 纳入每轮执行时限及次数预算。系统 SHALL 限制每个程序的 CPU、内存、进程数、临时存储及输出规模，并对同一构建链限制修复尝试。达到限额或用户取消时 SHALL 停止相关任务，返回明确状态，不继续在后台生成或执行。

#### Scenario: Repeated failures and cancellation
- **WHEN** 同一程序连续修复仍失败并达到预算，或用户取消运行
- **THEN** Agent 停止尝试并说明失败原因；执行后端确认任务已终止，其他任务不受影响

### Requirement: Honest tool execution events

现有 TKB 工具与自主工具入口的业务/传输/执行失败 SHALL 在 Pi 工具历史及 SSE 中一致标记失败。界面 SHALL 区分执行、测试、修复、发布、复用与失败，并保留当前回答中的失败记录；默认 SHALL 不公开生成源代码、完整任务输入、原始输出或秘密值。

#### Scenario: Correction after failed attempt
- **WHEN** 首次生成执行失败，修复后成功并保存
- **THEN** 用户能看到失败、重试、成功和保存状态，最终答案依据成功运行结果，失败尝试未被伪装成成功

### Requirement: Availability and evidence boundaries

健康信息 SHALL 报告执行后端是否可用、语言/镜像版本、网络能力和工具库状态，不运行模型或付费外部 API。Agent SHALL 获得这些真实能力信息；执行后端不可用时 SHALL 保留现有 TKB 功能并明确说明不能执行自建工具。生成工具的代码、描述、测试和网络内容 SHALL 作为不可信材料处理，不能通过其中的指令扩大执行权限或改写系统规则。

#### Scenario: Runner unavailable
- **WHEN** 执行服务停止而 MCP 仍正常
- **THEN** 现有知识库问答继续可用，自主执行能力报告不可用，Agent 不声称运行了代码

### Requirement: Acceptance without predefined business implementations

验收 SHALL 使用空工具库和真实模型，确认 Agent 为未预置的任务编写并运行代码、根据人为引入的失败修复，以及在后续会话复用发布版本。确定性测试 SHALL 验证执行/隔离契约；真实模型任务 SHALL 由独立预期结果验证，不能仅靠模型自己生成的测试断言工具正确。

#### Scenario: Independent unseen task verification
- **WHEN** 验收提供未硬编码的输入变体与独立预期输出
- **THEN** 只有记录到实际生成、执行及正确结果才通过；仅回复解释、复述代码或调用预置同名业务函数不得计为通过
