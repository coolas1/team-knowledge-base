# 方舟模型与图片式 PPT 操作手册

## 模型与凭据

本机验收配置（2026-09-12）：

```dotenv
LLM_MODEL=doubao-seed-2.1-turbo
LLM_BASE_URL=https://ark.cn-beijing.volces.com/api/plan/v3
LLM_API_KEY=<Agent Plan 凭据>
LLM_MEMORY_THINKING=disabled
PI_AGENT_IMAGE_INPUT=true
IMAGE_PROVIDER=ark
IMAGE_MODEL=doubao-seedream-5.0-lite
IMAGE_BASE_URL=https://ark.cn-beijing.volces.com/api/plan/v3
IMAGE_API_KEY=<明确配置的图片凭据>
PPT_ENABLED=true
```

Turbo 负责摘要、fact 提取、observation/consolidation、Agent 和视觉检查；Seedream 负责完整幻灯片图片与参考图生成。Embedding 配置独立。没有 `LLM_PROVIDER` 设置；Pi 的 `openai` provider 表示兼容协议，不代表调用 OpenAI 模型。

图片凭据没有文本凭据回退。经账户所有者授权，两项 key 可以显式配置为相同值；不得将值提交到 Git、前端或验收报告。当前实现只接受上述 Agent Plan 图片路由和模型；不自动切换到其他计费 endpoint。请求成功不能证明套餐抵扣成功：实际模型日期版本、AFP、人民币/美元费用或套餐归属以控制台账单为准，未知值保持 `null`。

记忆任务关闭思考，不改变普通聊天的思考策略。摘要与提取缓存包含模型、endpoint 和记忆思考策略；切换后旧身份的缓存不再命中。实际第一批验收覆盖 JSON、stream、tool、图片输入及完整记忆链，见 OpenSpec execution-log。

## 创建与审核

访问 `/ppt`，或让 Agent 使用 `tkb-image-ppt` skill 创建图片式 PPT。普通可编辑 PPT、DOCX 和 PDF 继续使用原文档工具。每页图片不可逐项编辑；讲稿写入 PowerPoint 备注。

1. 设置标题、风格、大纲、每页要点/版式/讲稿和必需参考图片。默认 8 页，最多 20 页。来源必须属于当前可见 scope，必需图片必须能读取原图。
2. 确认大纲才会生成第一页样张。Agent 的 approve 工具只给审核链接，不替用户批准。
3. 查看真实样张，确认文字、数字、画幅和风格后批准该 revision，才会继续其他页。
4. 逐页生图及 Turbo 视觉检查；每页最多一次自动修复。所有页通过后，固定上游脚本组装 PPTX，经 LibreOffice 实际打开和渲染验证后发布下载。
5. 页面刷新、聊天断线不丢任务。任务 ID 对应持久化状态；返回同一审核链接可继续。预览、来源、缓存与下载每次重新检查 scope、bank policy 和源内容身份。

上游固定到 `f47bd3e54e49d14d51807692694e2d5619a8e298`；构建检查文件 manifest，保留 MIT。串行持久化 worker 替代上游交互式子 agent 编排，详细差异见 `src/agent/ppt/UPSTREAM.md`。

## 额度、故障与缓存

- 默认图片请求上限为页数的两倍；QA 也单独统计请求和供应商 usage。图片超时、失败和修复均计入已预留尝试次数。使用次数硬上限控制自动重试，不能将请求数换算为未经核实的费用。
- token 预算使用请求前预留和返回 usage 结算；预留是保守估算，不是供应商报价。未知 usage 会阻止有 token 上限的后续请求。AFP/金额无可核实报价时暂停，不能声称满足金额预算。需要绝对账户额度上限时同时设置供应商账户限制。
- 请求结果未知时暂停，不自动重复付费。先在控制台核对 request ID/账单，确认后显式重试；请求可能已收费。不要删除数据库任务以“解除”预算。
- worker 使用租约、心跳及 revision fencing。文件和结果 manifest 已落盘时，恢复校验 hash 后继续 QA；不能确认结果则进入未知状态。取消后不启动新页，在途结果可结算但不会将任务变为完成。
- 同 scope、来源、页面内容、风格和 backend 的成功页可以复用。修改单页使该页失效；修改整体风格/模型/来源身份会影响复用。重新确认新 revision，旧批准不能沿用。权限撤销后，旧缓存和下载同样拒绝访问。
- 制品位于 artifacts 卷的 `ppt/<job>/revision-<n>/`：PPTX、原图、outline、speech、deck_spec、slide_jobs、validation 和实际渲染预览。成功前没有最终下载链接。

## 本机构建与回退

以下仅用于明确授权的本机开发容器。LAN `main` 部署仍由 `cicd/` 管线管理，不手工替代。

首次先保持 `PPT_ENABLED=false`，标准 `docker compose build webapp pi-agent` 后 recreate 两服务，检查 `/health`、`/version`、Pi `/health` 的 MCP 契约和记忆队列。文本链路验证后再开启 PPT。本功能仅添加 `ppt_jobs`、`ppt_pages`、`ppt_events`，不清理历史文档或记忆。

部署前保存当前容器镜像、私有 env、PostgreSQL dump 和 uploads 卷备份，并对 documents/chunks/memory_units/file_summaries 生成实时指纹。比较同一份实时基线，不能用历史 UI 条目数判断损坏。合成验收采用独立 bank，单独登记新增数据。

紧急停用先设 `PPT_ENABLED=false` 并 recreate webapp；待供应商在途请求明确结算，再考虑重试。回退到部署前镜像标签并恢复其 env；新增表可保留，旧业务数据无须回灌。只有确认需要数据库恢复且取得针对覆盖数据的授权时，才使用 dump；不要直接覆盖已有新业务写入。备份及验收图片含凭据或内容，保留在忽略的 `output/ark-ppt-acceptance/`，不进入 PR。

完整分批提交、真实 usage、测试与部署版本见 `openspec/changes/configure-ark-and-integrate-ppt-skill/execution-log.md`。

## 当前验收限制与待确认调整

本次三页真实 Agent 验收已使用全部 6 次生图：第一页通过，第二页因参考图被重绘而失败，第三页未生成，任务没有最终下载。多次明确图片用途和白底矩形保留要求，仍出现额外信息框、原图标题丢失或桥图标变化。不能把“API 支持参考图输入”解释为“原图内容必然保持不变”。本次发布包含工作流和拦截机制，尚未通过完整三页端到端交付验收。

建议下一次 OpenSpec 设计调整为：模型生成背景与非素材内容，并为必需原图预留明确区域；服务端根据已审核的区域坐标等比嵌入原图，再对最终合成页执行视觉 QA 和 PPTX 渲染。需要同步定义素材区域、重叠/裁切拒绝规则、原图 hash 及缓存版本，保留整页图片交付和现有审批/预算控制。该方案尚未实施，也未追加生图预算；确认设计后再安排新一轮有限额的三页验收，不降低当前检查标准。
