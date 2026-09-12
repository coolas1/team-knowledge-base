## Why

项目已填写方舟 Agent Plan 的 `doubao-seed-2.1-turbo` 配置，独立 JSON 探针成功，但已启动容器仍使用 DeepSeek，且后台记忆调用未对豆包配置思考策略。现有 PPT 仅将 Markdown 填入固定模板；需要引入 codex-ppt 的视觉规划、样张和逐页生图流程，同时让模型消耗、权限和失败续跑可验证。

## What Changes

- 第一批完成方舟配置加载、豆包有界 JSON 思考策略及容器内实测；尊重用户指定的 Turbo，不自行替换 Lite/Mini/Pro。保留 embedding 与 reranker 配置。
- 将记忆任务的思考策略与交互 Agent 的策略分离，记录真实 usage、实际模型版本与缺失计费信息；不以 HTTP 成功推断套餐抵扣成功。
- 新增独立的图片模型配置，目标为 Agent Plan 的 Seedream 5.0 lite；先验证该账号模型权限、文生图、参考图生成、图片下载和计费路径。
- 固定版本引入 codex-ppt 的必要流程、参考资料和组装能力，增加 Seedream 适配；明确记录与上游 GPT Image 专用校验、子 agent 调度规则的适配差异。
- 新增按 bank/tags 隔离的持久化 PPT 任务：大纲和风格、样张批准、逐页生成、视觉检查、受控修复、讲稿备注和 PPTX 下载。耗时工作由 worker 执行，支持取消、重启续跑和单页缓存。
- 保留现有普通可编辑 PPT/DOCX/PDF 路径；图片式 PPT 明示页面文字与图表不可逐项编辑，不伪造可编辑版本或下载链接。
- 分六批实施，每批验证后提交功能分支并记录 SHA；仅在最终验收后申请 PR 到 develop，不自动合并。

## Capabilities

### New Capabilities
- `ppt-generation`: 基于 skill 的分阶段图片式 PPT、持久化任务、权限、预算和交付验收。

### Modified Capabilities
- `model-config`: 增加独立图片模型组和用途分离的思考配置；兼容既有 LLM/embedding/reranker 契约并验证部署实际生效。

## Impact

- 后端：config/settings.py、config/schema.py、docker-compose.yml、Analyzer/Hindsight providers、附件存储与路由、新 PPT provider/worker/任务表；必要时扩展模型 provenance 与图片输入消息。
- Agent/前端：Pi 模型能力声明、MCP 契约、打包 skill、任务工具、进度/样张批准/取消/重试 UI。无需给模型开放通用宿主 shell 或密钥。
- 构建：Web/worker 镜像携带固定上游资源和所需渲染依赖；沿用标准 Containerfile、安全门禁及已有部署方式。
- 数据：增加任务与页面状态；已有文件、向量和记忆不重置，不将 PPT 产物自动再次写入记忆。
- 外部依赖：方舟 Agent Plan 文本与生图服务、ningzimu/codex-ppt-skill（MIT）；实际模型别名、参数与套餐抵扣须账户实测。
