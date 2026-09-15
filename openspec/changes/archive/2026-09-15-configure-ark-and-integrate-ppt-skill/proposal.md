## Why

项目已填写方舟 Agent Plan 的 `doubao-seed-2.1-turbo` 配置，独立 JSON 探针成功，但已启动容器仍使用 DeepSeek，且后台记忆调用未对豆包配置思考策略。现有 PPT 仅将 Markdown 填入固定模板；需要让用户在现有提问聊天界面直接要求 AI 调用 codex-ppt skill，完成视觉规划、逐页生图和 PPTX 交付，而不是进入另一套 PPT 产品界面。

## What Changes

- 第一批完成方舟配置加载、豆包有界 JSON 思考策略及容器内实测；尊重用户指定的 Turbo，不自行替换 Lite/Mini/Pro。保留 embedding 与 reranker 配置。
- 将记忆任务的思考策略与交互 Agent 的策略分离，记录真实 usage、实际模型版本与缺失计费信息；不以 HTTP 成功推断套餐抵扣成功。
- 新增独立的图片模型配置，目标为 Agent Plan 的 Seedream 5.0 lite；先验证该账号模型权限、文生图、参考图生成、图片下载和计费路径。
- 固定版本引入 codex-ppt 的必要流程、参考资料和组装能力，增加 Seedream 适配；明确记录与上游 GPT Image 专用校验、子 agent 调度规则的适配差异。
- 将固定版本的 codex-ppt skill 注册到现有聊天 Agent。用户以自然语言提出 PPT 请求后，AI 直接执行该 skill，并在当前会话中报告进度、请求必要输入以及返回最终 `.pptx` 附件；不新增或保留独立 PPT 页面、导航入口、任务 API、任务表、worker 或审批状态机。
- 必需参考图由 skill 的受控本地流程按固定区域等比嵌入，模型生成无字视觉背景，标题与要点由打包的 CJK 字体精确栅格化；最终合成页执行像素/来源校验与视觉 QA。生成过程受单次工具调用的页数、重试和模型请求预算约束。
- 保留现有 DOCX/PDF 路径；聊天中的所有 PPT 请求统一调用图片式 PPT skill，明示页面文字与图表不可逐项编辑，不伪造可编辑版本或下载链接。
- 已完成的七批作为实施历史保留；追加第八批，将已实现的独立 PPT 产品重构为聊天内 skill 调用，验证后提交当前功能分支。

## Capabilities

### New Capabilities
- `ppt-generation`: 在现有聊天 Agent 中直接调用固定 skill，生成有来源约束、预算边界和真实附件交付的图片式 PPT。

### Modified Capabilities
- `model-config`: 增加独立图片模型组和用途分离的思考配置；兼容既有 LLM/embedding/reranker 契约并验证部署实际生效。

## Impact

- 后端：保留独立图片模型配置、受控图片 provider、通用附件存储与授权下载；移除 PPT 专用 job/page/event 表、worker 和 `/api/ppt/jobs`。
- Agent/前端：Pi 注册并执行打包 skill；现有聊天消息流显示工具进度并交付附件，不设置 `/ppt` 页面或导航入口。无需给模型开放通用宿主 shell 或密钥。
- 构建：Web/Agent 镜像携带固定上游资源和所需渲染依赖；沿用标准 Containerfile、安全门禁及已有部署方式。
- 数据：PPTX 作为普通会话附件保存；已有文件、向量和记忆不重置，不将 PPT 产物自动再次写入记忆。
- 外部依赖：方舟 Agent Plan 文本与生图服务、ningzimu/codex-ppt-skill（MIT）；实际模型别名、参数与套餐抵扣须账户实测。
