## Context

动机见 proposal.md。2026-09-12 基线：develop `86a9ba12e`，用户 .env 已指定 `doubao-seed-2.1-turbo` 和 `https://ark.cn-beijing.volces.com/api/plan/v3`；此前最小 JSON 请求 HTTP 200，解析到实际版本 `doubao-seed-2-1-turbo-260628`，usage 101 tokens（39 reasoning tokens）。这只证明文本接口可用，不证明完整记忆链路、生图或套餐抵扣。Docker 重启后健康，但 Pi /health 仍报 DeepSeek；当前可见记忆为 132，后续验收以运行前实时快照为准，不能恢复到旧的 108 条基线。

项目 LLM_PROVIDER 惰性忽略，Pi 没有单独模型覆盖时继承 LLM_*；模型注册仍声明 text-only。Analyzer/Hindsight 共用模型，但 bounded_json_options 只处理 DeepSeek。现有 generate_document 为同步产物工具（在线程中执行），无多阶段 PPT 任务。附件下载已有可信 scope 校验。

## Goals / Non-Goals

**Goals:** 在真实容器中验证用户指定 Turbo；独立控制记忆思考与生图消耗；完成可预览、可续跑、有证据的图片式 PPT。现有普通 PPT 和会话体验不回退。

**Non-Goals:** 不更换 embedding、不重建历史记忆、不自动购买套餐或开启超额付费、不提供图片元素自动转可编辑功能、不宣称达到 GPT Image 相同画质。首版不允许工具动态安装任意 skill 或执行模型生成的宿主脚本。

## Decisions

### 1. 模型配置与用途分离

沿用 LLM_MODEL/BASE_URL/API_KEY，初始为用户指定 Turbo；LLM_PROVIDER 可从本机配置清理，不新增 provider 枚举。新增 `LLM_MEMORY_THINKING=auto|disabled|enabled`：默认 auto 保留既有兼容行为；本机方舟在真实探针支持后设置 disabled。只对明确支持的模型/端点传相应参数，unsupported 配置 fail fast，不静默回到高开销模式。覆盖摘要、retain、consolidation 和 mental-model refresh，且记忆参数不改变交互 Agent 设置。调用记录包含请求别名、响应实际版本和有效思考策略；策略进入摘要/抽取缓存身份，旧资料仍有效但不误用不同策略的缓存。

独立 `IMAGE_PROVIDER=ark`、`IMAGE_BASE_URL`、`IMAGE_MODEL`、`IMAGE_API_KEY`；空 URL 关闭图片式 PPT；启用时必须完整配置。候选套餐名称 `doubao-seedream-5.0-lite`，最终以账户实际可调用名称为准，不拼接普通按量模型版本。图片 key 不隐式继承文本 key，不回退 `/api/v3`，不切换 GPT Image 服务。新增 `PPT_ENABLED`、worker 并发/预算选项。配置读取仅输出 key 是否存在，不输出值。

不采用单一 LLM_* 同时配置文生图，避免错误调用 chat/completions 和混淆额度。升级到 Mini/Lite/Pro 路由留作后续优化，本变更不擅自替换用户模型。

### 2. 固定上游，显式适配

目标上游 `ningzimu/codex-ppt-skill` commit `f47bd3e54e49d14d51807692694e2d5619a8e298`。实施第二批读取此固定版本的 SKILL、工作流门禁、组装、素材、worker 与 provider 文档，保留 MIT/归属、upstream manifest 和差异表。必要代码与资源 vendor 到仓库受控目录，构建期校验，不在运行期下载 main。

保留大纲、统一风格、样张、逐页生成、记录、QA、讲稿和组装逻辑。上游 GPT Image 模型名校验和专用参数改为 provider 能力校验；Ark 参考图走自身 JSON image 输入契约，不机械发送 OpenAI multipart edits。支持实际返回 URL/base64 并立即持久化，下载限制大小、类型、超时和重定向目标；不得允许任意内网 URL。

Pi 当前没有子 agent 调度工具；首版使用持久化逐页 worker、默认串行，替代上游子 agent 步骤，并在本地 skill 中明确这项产品适配，不伪称使用了子 agent。每页最终图仍须由配置生图后端生成；不能用文本模板截图冒充成功。未来并发上限可调，但预算预留必须先原子获取。

### 3. 异步任务与持久化数据

新增 PPT job/page 关系表及追加事件；存储 job_id、bank/tags、owner/session、revision、outline/style/backend 指纹、审批版本、预算预留、lease、页面状态、实际模型/usage、产物 checksum 和错误类型。文件置于 artifacts 卷内 job 专属目录；临时文件原子发布后登记，跨 bank 禁止缓存复用。

任务状态：draft → awaiting_outline_approval → awaiting_sample_approval → queued → generating → reviewing → assembling → completed；分支 paused_budget / failed / cancelled。样张生成发生在大纲、风格、后端确认之后，样张批准之后才允许其余页面生成。审批通过明确 UI 操作绑定 revision；用户普通聊天文本不能作为后台隐式批准。

工具/HTTP 提交后快速返回 job id，worker 处理耗时调用，不消耗单次聊天超时。租约、心跳和 generation fencing 拒绝旧 worker 写入。重启仅重跑未完成页；网络断开且无法判断生图是否已成功时标记结果未知，先恢复结果或显式单页重试，不能宣称 exactly-once provider 计费。取消阻止新调用，无法撤回的在途调用单独结算且不能把 cancelled 发布为 completed。

### 4. 页面身份、预算与 QA

缓存键包含 scope、内容/来源 revision、页面文案、全局风格、参考图 checksum、上游版本、模型实际可解析身份、尺寸和 provider 参数；页面修改只失效自身，风格/后端修改失效全部并重新要求样张确认。按内容散列复用只限可访问任务。供应商别名可能漂移：记录实际返回模型，不能承诺不同日期结果一致。

默认 8 页、硬上限 20 页、默认 16:9 2K（仅在实际模型允许该尺寸时启用），最多每页 1 次自动修复，每任务最多 2×页数个生成尝试，默认并发 1。开始前原子预留请求次数与可配置 token/AFP/金额上限；无 AFP/价格数据则显示未知并以请求次数硬限额，不能填 0 元。样张若属于正文页可直接复用。重试也占预算。

视觉 QA 通过 Turbo 图片输入能力探针后启用：检查中文、数值、标题、截断、参考素材、风格与布局。模型检查结合本地尺寸/文件校验和最终人工预览，不能保证模型自检等于无误。精确数字/引用绑定来源，必要原图必须实际提供给生图后端。未通过页不能打包为最终成功。

### 5. 产品/API 兼容

保留 generate_document 既有契约；图片式 PPT 采用专用 create/status/approve/retry/cancel 工具和 `/api/ppt/jobs` 路由，不让原同步工具悄悄返回另一种结构。Pi 读取打包 skill，使用显式工具调用；图片输入能力通过配置/探针声明并验证工具结果到模型的真实图片传递，不仅将 text 改为 image 标志。

前端提供任务进度、大纲与样张预览、审批、取消、单页重试及最终下载；重连拉取持久化状态。最终 PPTX 内每页一张整页图，讲稿进入备注；附大纲/讲稿/预览，不将它们称为可编辑页面。artifact 下载沿用可信 binding，页面和参考图接口同样授权。删除资料或权限撤销后再次读取与执行均重新校验；任务撤销授权后不再调用模型。

### 6. 验收和操作证据

第一批只做配置兼容和文本链路：标准镜像 build、受授权本机 compose recreate（restart 不加载 env）、健康与版本检查、JSON/stream/tool-call/视觉输入探针；独立 bank 合成 fixture 验证摘要→facts→observation→查询，保留 provider usage。默认烟测额度合计最多 10000 输出 tokens，最多 12 次生成请求；不足则暂停记录，不能自动扩大。

第二批独立生图探针最多 2 张（样张+带参考图版本）；第六批采用 3 页合成内容端到端（至多 6 次生成请求），测试中断/单页续跑优先注入本地故障，不为故障测试重复付费。正式用户资料不会用于公共 fixture。所有请求保留脱敏耗时、usage、模型和校验结论；没有真实服务成功不得勾选最终验收。

## Risks / Trade-offs

- [模型可用但套餐不抵扣] → 仅配置 Agent Plan 独立路由与密钥，记录控制台用量证据；未能核实后台任务适用范围则阻止真实后台放量。
- [Seedream 参数与 GPT Image 不同] → provider 契约测试覆盖参数白名单、参考图与下载，不做只改模型名的适配。
- [图片中文/数字错误且不可编辑] → 样张批准、受限自动修复、来源核对和明确图片式交付说明。
- [调用超时造成重复计费] → 保存 request id/未知状态，原子预算与结果恢复；没有供应商幂等能力时不作 exactly-once 承诺。
- [scope 与异步 worker 脱节] → 执行/审批/缓存/下载均校验可信权限，跨 bank/撤销/过期 lease 必须真实测试。
- [纯配置修改未进入容器] → 同时核对 webapp 与 Pi 实际配置，不能只看 .env 或镜像 SHA。

## Migration Plan

实施从 develop 创建 `feat/ark-ppt-generation`，不直接在 develop 写实现。六批分别提交并在 execution-log 记录 SHA 与检查结果。新增表使用幂等 additive migration；PPT 功能初始关闭，先完成文本迁移，再启用生图。保留现有文件、记忆、向量和数据卷，部署前导出数据库与上传文件恢复材料。

本机部署沿用用户已明确授权的 Docker 更新范围，不扩展到 LAN main 的发布。失败时关闭 PPT flag/worker，保留任务材料，以上一镜像和已保存本机模型配置恢复；数据库已有新写入时只前向修复，不覆盖旧备份。最终 PR 目标 develop，推送/创建 PR 在用户实施授权范围内办理，绝不自动合并。

## Open Questions

- Seedream 套餐确切模型名、参考图参数、图片输出尺寸与 AFP 用量字段，由第二批真实探针确定；不改变分阶段生图方案。
- Turbo 图片与工具调用组合的实际限制、关闭思考的响应 usage 字段，由第一批兼容测试确定；不支持则明确阻塞相关功能，不静默换模型。

## Sources

- 项目配置与实际健康接口；前序 Turbo 101-token 探针是历史证据，不当成本轮测试。
- https://github.com/ningzimu/codex-ppt-skill/tree/f47bd3e54e49d14d51807692694e2d5619a8e298
- https://github.com/volcengine/ark-cli/blob/main/skills/arkcli-gen/references/arkcli-gen.md
- https://github.com/volcengine/ark-cli/blob/main/skills/arkcli-plans/references/arkcli-plans-model-list.md
- https://console.volcengine.com/ark/region:cn-beijing/subscription/agent-plan
