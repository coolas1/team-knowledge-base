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

Turbo 负责摘要、fact 提取、observation/consolidation、Agent 和视觉检查；Seedream 负责幻灯片背景与非素材内容，必需原图由程序等比嵌入最终图片。Embedding 配置独立。没有 `LLM_PROVIDER` 设置；Pi 的 `openai` provider 表示兼容协议，不代表调用 OpenAI 模型。

图片凭据没有文本凭据回退。经账户所有者授权，两项 key 可以显式配置为相同值；不得将值提交到 Git、前端或验收报告。当前实现只接受上述 Agent Plan 图片路由和模型；不自动切换到其他计费 endpoint。请求成功不能证明套餐抵扣成功：实际模型日期版本、AFP、人民币/美元费用或套餐归属以控制台账单为准，未知值保持 `null`。

记忆任务关闭思考，不改变普通聊天的思考策略。摘要与提取缓存包含模型、endpoint 和记忆思考策略；切换后旧身份的缓存不再命中。实际第一批验收覆盖 JSON、stream、tool、图片输入及完整记忆链，见 OpenSpec execution-log。

## 在聊天中创建

进入现有“提问”页面，直接要求知识库助手根据当前对话或指定资料生成 PPT。Agent 读取打包的 `tkb-image-ppt` skill，并调用一次 `tkb_generate_image_ppt`。应用没有独立 `/ppt` 页面、审核入口或后台 PPT 任务。通用文档工具只生成 DOCX 和 PDF；聊天中的所有 PPT 请求统一走图片式 PPT skill。图片式 PPT 的每页是一张完整图片，元素不可逐项编辑；讲稿写入 PowerPoint 备注。长时间生图和视觉检查期间，SSE 心跳会保持聊天连接。

1. Agent 从已授权资料整理标题、风格、大纲、每页要点、版式和讲稿。缺少必要信息时在聊天中询问。默认 8 页，最多 20 页。
2. 工具在当前聊天调用中生成内部风格样张和各页无字视觉底图。标题与要点使用容器内 Noto 中文字体精确栅格化；带必需原图的页面使用上下条带与中央保留区，程序按顺序等比嵌入原图，不裁切、不重绘。
3. Turbo 对最终页面核对文字、数字、布局和原图；每页最多自动修复一次。全部通过后，固定上游脚本组装 PPTX，并用 LibreOffice 实际打开和渲染验证。
4. 成品登记为当前 scope 下的普通 artifact。Agent 在同一条回答中返回 `/api/artifacts/<id>/download` 链接；聊天记录保留该链接，刷新后仍可下载。
5. 中断或失败会停止本次调用，不会在后台隐藏重试。重新生成可能产生新的费用，需由用户再次明确提出。

上游固定到 `f47bd3e54e49d14d51807692694e2d5619a8e298`；构建检查文件 manifest，保留 MIT。一次受控的前台执行器替代上游交互式子 agent 编排，详细差异见 `src/agent/ppt/UPSTREAM.md`。

## 额度、故障与缓存

- 默认图片请求上限为页数的两倍；图片超时、失败和自动修复均计入本次调用。QA 请求和供应商 usage 单独累计；无法核实的 AFP 和金额明确显示为 unknown。
- Agent 每个聊天 turn 最多实际调用一次图片式 PPT 工具；失败结果会直接回到当前聊天，不允许模型在同一 turn 内自行重新生成。图片尝试数由后端按页数计算，Agent 不能扩大。
- 每次 provider 调用前检查剩余图片次数。达到上限立即停止，不创建另一个任务绕过限制。
- 请求结果未知时停止本次执行，不自动重复付费。重新调用前应核对返回的错误和供应商账单；请求可能已收费。
- 中间图片只保存在本次调用的临时目录，不跨会话缓存或复用。只有全部页面通过 QA 和组装验证后，最终 PPTX 才进入通用 artifacts 目录。
- 来源在读取和组装前各校验一次。权限或内容发生变化时拒绝发布；最终下载继续使用 artifact 的 scope 校验。

## 本机构建与回退

以下仅用于明确授权的本机开发容器。LAN `main` 部署仍由 `cicd/` 管线管理，不手工替代。

首次先保持 `PPT_ENABLED=false`，标准 `docker compose build webapp pi-agent` 后 recreate 两服务，检查 `/health`、`/version`、Pi `/health` 的 MCP 契约和记忆队列。文本链路验证后再开启 PPT。旧版本创建的 `ppt_jobs`、`ppt_pages`、`ppt_events` 表停止读写并暂时保留，部署不执行破坏性删表。

部署前保存当前容器镜像、私有 env、PostgreSQL dump 和 uploads 卷备份，并对 documents/chunks/memory_units/file_summaries 生成实时指纹。比较同一份实时基线，不能用历史 UI 条目数判断损坏。合成验收采用独立 bank，单独登记新增数据。

紧急停用先设 `PPT_ENABLED=false` 并 recreate webapp；待供应商在途请求明确结算，再考虑重新生成。回退到部署前镜像标签并恢复其 env；历史表可保留，旧业务数据无须回灌。只有确认需要数据库恢复且取得针对覆盖数据的授权时，才使用 dump；不要直接覆盖已有新业务写入。备份及验收图片含凭据或内容，保留在忽略的 `output/ark-ppt-acceptance/`，不进入 PR。

完整分批提交、真实 usage、测试与部署版本见 `openspec/changes/configure-ark-and-integrate-ppt-skill/execution-log.md`。

## 原图保真调整与历史验收

本次三页真实 Agent 验收已使用全部 6 次生图：第一页通过，第二页因参考图被重绘而失败，第三页未生成，任务没有最终下载。多次明确图片用途和白底矩形保留要求，仍出现额外信息框、原图标题丢失或桥图标变化。不能把“API 支持参考图输入”解释为“原图内容必然保持不变”。该轮失败证据保留，不视为成功交付。

用户已批准并实施 `reference-composite-v1`：程序将原图等比放入固定保留区，不裁切、不重绘。原图按 EXIF 方向解码，透明区域采用白色底；仅进行等比缩放，缩放后像素可逐点核对。画布 2560×1440，保留区 x=384、y=288、width=1792、height=864；多图平分区域，间隔 51 像素。生成模型只负责区域外标题、要点和背景，原图通过本地合成与最终视觉检查保证保真。

背景原文件、原图 hash、实际嵌入矩形和像素 hash 随任务保存。最终无损 PNG 的字节必须与 PPTX 内嵌图片完全一致；上游可选 JPEG 压缩在适配层禁用，固定 vendor 文件不修改。策略版本进入 backend/cache 身份，旧任务必须修改保存为新 revision 并重新审批，不能直接复用旧策略结果。

本轮新验收独立限制为最多 6 次生图，结果与实际 usage 记录在 execution-log 的补充批次；旧任务不重置预算。

本轮三页验收已完成：6 次生图和 6 次视觉 QA 后成功下载，逐页人工核对 LibreOffice 渲染图及讲稿，原图缩放后像素和 PPTX 内嵌 PNG 字节校验通过。随后整套三页缓存复用为零生图、零 QA。保留每次失败尝试与费用记录；生成页外文字仍可能需要修复，不能保证所有文案一次通过。

## 聊天直调验收

2026-09-12 从 `/ask` 使用的同一 BFF 会话接口完成三页真实验收。Agent 先读取固定 `tkb-image-ppt` skill，再执行一次 `tkb_generate_image_ppt`；没有访问 `/ppt`、创建 job、等待审批或启动后台 worker。成品登记为普通 artifact，刷新会话后下载链接仍存在，历史 `/api/ppt/jobs` 返回 404。

三页各使用一次 Seedream 底图和一次 Turbo 视觉 QA，共 3 次生图、3 次 QA、51,167 tokens，未知 usage 为 0；图片模型返回 `doubao-seedream-5.0-lite`，Turbo 实际版本为 `doubao-seed-2-1-turbo-260628`。AFP、套餐抵扣和货币费用仍为 unknown。LibreOffice 25.2.3.2 成功打开 951,608 字节的 PPTX 并渲染为三页 PDF；三页均含讲稿，人工检查中文与 `≤ 30秒/页`、`≥ 95%` 数值清晰正确。
