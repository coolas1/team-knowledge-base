## Context

动机见 proposal.md。2026-09-12 基线：develop `86a9ba12e`，用户 .env 已指定 `doubao-seed-2.1-turbo` 和 `https://ark.cn-beijing.volces.com/api/plan/v3`；此前最小 JSON 请求 HTTP 200，解析到实际版本 `doubao-seed-2-1-turbo-260628`，usage 101 tokens（39 reasoning tokens）。这只证明文本接口可用，不证明完整记忆链路、生图或套餐抵扣。Docker 重启后健康，但 Pi /health 仍报 DeepSeek；当前可见记忆为 132，后续验收以运行前实时快照为准，不能恢复到旧的 108 条基线。

项目 LLM_PROVIDER 惰性忽略，Pi 没有单独模型覆盖时继承 LLM_*；模型注册仍声明 text-only。Analyzer/Hindsight 共用模型，但 bounded_json_options 只处理 DeepSeek。现有 generate_document 为同步产物工具（在线程中执行），无多阶段 PPT 任务。附件下载已有可信 scope 校验。

## Goals / Non-Goals

**Goals:** 在真实容器中验证用户指定 Turbo；独立控制记忆思考与生图消耗；让用户在现有聊天界面直接要求 AI 调用固定 codex-ppt skill，并在同一会话收到有证据的图片式 PPT 附件。现有普通 PPT 和会话体验不回退。

**Non-Goals:** 不更换 embedding、不重建历史记忆、不自动购买套餐或开启超额付费、不提供图片元素自动转可编辑功能、不宣称达到 GPT Image 相同画质。首版不允许工具动态安装任意 skill 或执行模型生成的宿主脚本。

## Decisions

### 1. 模型配置与用途分离

沿用 LLM_MODEL/BASE_URL/API_KEY，初始为用户指定 Turbo；LLM_PROVIDER 可从本机配置清理，不新增 provider 枚举。新增 `LLM_MEMORY_THINKING=auto|disabled|enabled`：默认 auto 保留既有兼容行为；本机方舟在真实探针支持后设置 disabled。只对明确支持的模型/端点传相应参数，unsupported 配置 fail fast，不静默回到高开销模式。覆盖摘要、retain、consolidation 和 mental-model refresh，且记忆参数不改变交互 Agent 设置。调用记录包含请求别名、响应实际版本和有效思考策略；策略进入摘要/抽取缓存身份，旧资料仍有效但不误用不同策略的缓存。

独立 `IMAGE_PROVIDER=ark`、`IMAGE_BASE_URL`、`IMAGE_MODEL`、`IMAGE_API_KEY`；空 URL 关闭图片式 PPT；启用时必须完整配置。候选套餐名称 `doubao-seedream-5.0-lite`，最终以账户实际可调用名称为准，不拼接普通按量模型版本。图片 key 不隐式继承文本 key，不回退 `/api/v3`，不切换 GPT Image 服务。新增 `PPT_ENABLED` 及 skill 单次调用的并发/预算选项。配置读取仅输出 key 是否存在，不输出值。

不采用单一 LLM_* 同时配置文生图，避免错误调用 chat/completions 和混淆额度。升级到 Mini/Lite/Pro 路由留作后续优化，本变更不擅自替换用户模型。

### 2. 固定上游，显式适配

目标上游 `ningzimu/codex-ppt-skill` commit `f47bd3e54e49d14d51807692694e2d5619a8e298`。实施第二批读取此固定版本的 SKILL、工作流门禁、组装、素材、worker 与 provider 文档，保留 MIT/归属、upstream manifest 和差异表。必要代码与资源 vendor 到仓库受控目录，构建期校验，不在运行期下载 main。

保留大纲、统一风格、内部样张、逐页生成、记录、QA、讲稿和组装逻辑。内部样张用于统一后续页面风格，不产生用户审批阶段。上游 GPT Image 模型名校验和专用参数改为 provider 能力校验；Ark 参考图走自身 JSON image 输入契约，不机械发送 OpenAI multipart edits。支持实际返回 URL/base64 并立即写入本次执行目录，下载限制大小、类型、超时和重定向目标；不得允许任意内网 URL。

Pi 当前没有子 agent 调度工具；首版由一次 skill 工具调用内的受控执行器默认串行处理页面，替代上游子 agent 步骤，并在本地 skill 中明确这项产品适配，不伪称使用了子 agent。每页无字视觉背景由配置生图后端生成；标题与要点使用打包的 CJK 字体精确栅格化，必需原图由本地程序合成到最终图片。未来并发上限可调，但每次 provider 调用前必须检查本次执行预算。

### 3. 聊天内 Skill 执行与附件交付

Pi 在现有聊天 Agent 中注册固定版本的 codex-ppt skill。模型识别“生成 PPT”等明确意图后调用受控工具入口；工具在一次 Agent 执行中完成资料读取、大纲与视觉规划、逐页生成、QA、讲稿和 PPTX 组装。工具进度通过现有聊天工具事件显示，不创建第二套页面和状态中心。

skill 可以在确有必要时通过正常对话请求标题、页数、受众或风格信息；用户回答后产生新的工具调用。用户发出生成请求即授权本次生成，不设置大纲/样张审批状态机。模型不得动态安装其他 skill、执行任意 shell，或读取会话权限之外的资料。

最终 PPTX 先原子写入通用 artifact 存储，再注册为当前会话附件，并由现有授权下载机制返回真实链接。聊天记录保存工具调用结果与附件引用；刷新后附件仍可访问，但不承诺恢复进程内中断的生成。失败时返回具体阶段和已发生的 provider 用量；用户可在后续对话中重新生成或要求修改，系统不以隐藏后台任务自动续跑。

### 4. 页面身份、预算与 QA

单次执行内的页面缓存键包含 scope、内容/来源 revision、页面文案、全局风格、参考图 checksum、上游版本、模型实际可解析身份、尺寸和 provider 参数。缓存只服务同一次受控执行及显式重做，不引入 PPT 专用数据库状态；跨会话不复用私有中间产物。供应商别名可能漂移：记录实际返回模型，不能承诺不同日期结果一致。

默认 8 页、硬上限 20 页、默认 16:9 2K（仅在实际模型允许该尺寸时启用），最多每页 1 次自动修复，每次工具执行最多 2×页数个生成尝试，默认并发 1。执行前检查配置的请求次数与 token/AFP/金额上限；无 AFP/价格数据则显示未知并以请求次数硬限额，不能填 0 元。重试也占预算。

视觉 QA 通过 Turbo 图片输入能力探针后启用：检查本地栅格化后的中文、数值、标题、截断、参考素材、风格与布局。模型检查结合本地尺寸/文件校验和最终人工预览，不能保证模型自检等于无误。精确数字/引用绑定来源，必要原图必须提供给本地合成器及视觉 QA；无必需原图的页面可发送无字风格样张；有必需原图的页面使用文字风格与固定中央保留区指令，不发送样张以避免复制其布局。未通过页不能打包为最终成功。

### 5. 产品/API 兼容

保留 generate_document 的 DOCX/PDF 行为，但从 Pi 工具 schema 移除 PPTX 选项，避免自然语言 PPT 请求绕过图片 skill。所有 PPT/PPTX/PowerPoint 请求使用独立的 Agent skill 工具，返回现有聊天协议可表达的进度事件、结果摘要和附件引用。Pi 读取打包 skill，使用显式工具调用；图片输入能力通过配置/探针声明并验证工具结果到模型的真实图片传递，不仅将 text 改为 image 标志。生图和视觉 QA 的静默期由 Pi SSE comment heartbeat 保持连接，BFF 原样中继，浏览器忽略 comment。

前端只复用现有提问聊天页面和通用工具状态/附件消息，不提供 `/ppt` 页面、PPT 导航或独立审核界面。最终 PPTX 内每页一张整页图，讲稿进入备注；结果明示图片页不可逐项编辑。artifact 下载沿用可信 binding，输入资料与参考图也沿用当前会话权限。删除资料或权限撤销后再次读取或重新生成时重新校验。

### 6. 验收和操作证据

第一批只做配置兼容和文本链路：标准镜像 build、受授权本机 compose recreate（restart 不加载 env）、健康与版本检查、JSON/stream/tool-call/视觉输入探针；独立 bank 合成 fixture 验证摘要→facts→observation→查询，保留 provider usage。默认烟测额度合计最多 10000 输出 tokens，最多 12 次生成请求；不足则暂停记录，不能自动扩大。

第二批独立生图探针最多 2 张（样张+带参考图版本）；第六批采用 3 页合成内容端到端（至多 6 次生成请求）。第八批从现有提问页面发起自然语言请求，验证 AI 调用 skill 并返回可下载附件；故障测试优先使用本地注入，不为故障测试重复付费。正式用户资料不会用于公共 fixture。所有请求保留脱敏耗时、usage、模型和校验结论；没有真实服务成功不得勾选最终验收。

## Risks / Trade-offs

- [模型可用但套餐不抵扣] → 仅配置 Agent Plan 独立路由与密钥，记录控制台用量证据；未能核实后台任务适用范围则阻止真实后台放量。
- [Seedream 参数与 GPT Image 不同] → provider 契约测试覆盖参数白名单、参考图与下载，不做只改模型名的适配。
- [图片中文/数字错误且不可编辑] → 内部样张约束、受限自动修复、来源核对和明确图片式交付说明。
- [调用超时造成重复计费] → 保存当前调用可得的 request id 与 usage，失败即停止本次执行；重新生成前提示可能重复计费，不作 exactly-once 承诺。
- [skill 访问越权资料] → 工具只接收当前会话已授权的资料引用，读取和附件下载均复用可信权限校验。
- [纯配置修改未进入容器] → 同时核对 webapp 与 Pi 实际配置，不能只看 .env 或镜像 SHA。

## Migration Plan

实施从 develop 创建 `feat/ark-ppt-generation`，不直接在 develop 写实现。前七批及 SHA 作为历史证据保留；第八批删除错误引入的 PPT 专用页面、路由、worker 和表结构使用点，将能力迁移到聊天 Agent skill。若数据库升级已创建专用表，先停止读写并保留兼容迁移，不在部署过程中破坏性删表。保留现有文件、记忆、向量和数据卷，部署前导出数据库与上传文件恢复材料。

本机部署沿用用户已明确授权的 Docker 更新范围，不扩展到 LAN main 的发布。失败时关闭聊天 PPT skill，以上一镜像和已保存本机模型配置恢复；数据库已有历史 PPT 记录时只停止使用并前向兼容，不覆盖旧备份。最终 PR 目标 develop，推送/创建 PR 在用户实施授权范围内办理，绝不自动合并。

## Open Questions

- Seedream 套餐确切模型名、参考图参数、图片输出尺寸与 AFP 用量字段，由第二批真实探针确定；不改变分阶段生图方案。
- Turbo 图片与工具调用组合的实际限制、关闭思考的响应 usage 字段，由第一批兼容测试确定；不支持则明确阻塞相关功能，不静默换模型。

## Sources

- 项目配置与实际健康接口；前序 Turbo 101-token 探针是历史证据，不当成本轮测试。
- https://github.com/ningzimu/codex-ppt-skill/tree/f47bd3e54e49d14d51807692694e2d5619a8e298
- https://github.com/volcengine/ark-cli/blob/main/skills/arkcli-gen/references/arkcli-gen.md
- https://github.com/volcengine/ark-cli/blob/main/skills/arkcli-plans/references/arkcli-plans-model-list.md
- https://console.volcengine.com/ark/region:cn-beijing/subscription/agent-plan

### 7. 原图保真调整（2026-09-12）

用户已确认调整并授权新一轮最多 6 次生图。采用版本化 `reference-composite-v1` 固定区域策略：画布 2560×1440，原图区域为 x=15%、y=20%、width=70%、height=60%；多张原图按输入顺序水平平分，间隔 2%。原图在各自区域内等比缩放居中，不裁切、不重绘；标题在上方、要点在下方。skill 在执行记录中保存服务器计算的每张图归属和位置。首版不提供任意坐标编辑，区域策略属于 backend 身份；原图顺序、checksum、策略版本进入页面身份。

生图 prompt 要求无字背景并明确预留空白区域；有必需原图时不向生成模型发送图片，无必需原图的页面可发送无字风格样张。程序用强色彩遮罩抑制底图偶发伪文字，再以 Noto CJK 精确绘制标题与要点。原图由合成器读取当前已授权字节。保留背景原图和实际 provider usage/request ID，合成后保存无损 PNG，并记录原图 hash、实际像素矩形和缩放后像素 hash。视觉 QA 和组装均检验最终图片和嵌入区域身份；越界、重叠、来源变化或像素变化拒绝发布。视觉 QA 查看合成后的图片及原图，最终 PPTX 必须保持嵌入 PNG 不进行有损重编码。

旧六次验收失败独立保留，不重置其预算。新的三页验收调用有独立最多 6 次生图上限；完成聊天入口生成、原图像素核对、实际渲染和附件下载后再勾选第八批验收。
