# 执行记录

## 2026-09-12 提案基线

- 当前工作区 develop `86a9ba12e`；保留三处既有换行状态及 output/，本轮不修改业务代码、.env 或运行容器。
- Docker 已启动，webapp /health=200，/version=0.2.1/86a9ba12e；Pi /health=ok、MCP=ok，但实际模型仍为 DeepSeek 官方旧配置。方舟配置尚未在容器生效。
- 当前可见记忆为 132，不能用此前 108 的验收基线覆盖后续数据。
- 之前 Turbo 最小 JSON 调用成功（101 tokens，其中 reasoning 39），是前序证据；本轮没有付费生图或新的模型生成调用。
- 上游固定提交 f47bd3e54e49d14d51807692694e2d5619a8e298 已从 GitHub 核实。暂未安装/修改上游资源。
- 本轮产物：proposal、design、两份 delta specs、六批 29 项未执行任务。
- 实施阶段须在此按批追加：代码 SHA、配置摘要（不含 key）、测试命令及结果、实际模型/request id/usage、预算、容器版本、恢复与可视验收证据位置。私有材料仅放非提交 output/。

## 第一批实施与验收（2026-09-12）

- 沿用用户已创建的 feat/ppt-skill-upd；HEAD 与 fetch 后 origin/develop 同为 86a9ba12e，无新提交需合并。原有 .gitignore、三处换行改动未纳入本批。
- 恢复材料：output/ark-ppt-acceptance/ 下 env.before（私有）、postgres.before.dump、uploads.before.tar.gz、images.before.txt；镜像保留 :before-ark-ppt。
- 业务数据指纹前后完全一致：documents 12、chunks 14、memory_units 205（含非活动记录）、file_summaries 2。与 UI 活动记忆数不是同一口径。
- LLM_MEMORY_THINKING=disabled 已在本机 .env 与 webapp 生效；Pi 保持独立交互设置，实际模型和 endpoint 均已切换到方舟 Turbo。无效 LLM_PROVIDER 已清理，embedding 未改。
- 新增记忆策略能力检查、摘要/抽取缓存身份、脱敏 requested/actual model 与 usage 日志。摘要、retain、consolidation、mental-model refresh 共用调用路径覆盖；不支持的显式策略拒绝。
- 真实探针 JSON/stream/tool/vision 全部 200；实际模型 doubao-seed-2-1-turbo-260628。四项使用 41 输出 tokens。
- 独立 tkb_ark_ppt_acceptance 数据库的 ark-synthetic bank：摘要成功、抽取 3 facts、生成 3 observations、引用回答正确保留林青/2026-10-15/120万元。查询脚本最初误用 quick，未发请求即失败；修正为 fast 后只续跑查询，没有重做记忆。
- 合计 9 次生成调用、1078 输出 tokens、5361 total tokens，每次 reasoning_tokens=0。无套餐抵扣证据，AFP/费用未知，未做后台批量调用。
- 测试：pytest 默认全库 456 passed / 39 skipped；Hindsight 204 passed；最终新增策略测试 8 passed；隔离 PostgreSQL 摘要迁移/缓存测试 1 passed；ruff check 通过。默认全库出现现有 Starlette 弃用及 Windows SSE 清理 warning，退出码 0。
- 标准 compose build webapp/pi-agent 成功；仅 recreate 两服务，health/MCP 均正常。初次验收镜像标记 86a9ba12e-ark-memory-wip，提交后用本批 SHA 重建标记。
- 完成提交使用 feat(engine): configure Ark memory reasoning；SHA 在后续执行记录追加（避免提交自身 SHA 循环）。

## 第二批实施与验收（2026-09-12）

- 第一批提交 f434fd40bba4069d2ccd347fe4185e78046794cc。
- 用户明确授权将现有 Agent Plan LLM_API_KEY 显式配置到独立 IMAGE_API_KEY；本机已写入独立 IMAGE_*，PPT_ENABLED=false。密钥不进入 Git、不进入 Pi/前端。
- 固定上游 41 个文件，Git blob hash 全量比对通过，SHA-256 manifest 和 MIT 保留；镜像构建增加离线校验，差异见 src/agent/ppt/UPSTREAM.md。raw 下载曾有 TLS 中断，大归档下载取消，最终只补取缺失 blob，未换版本。
- Seedream 请求白名单、真实参考图 data URI、单图 2560x1440、URL/base64、安全限额下载、IP pinning/SNI、无自动重试已实现；13 项 provider 测试通过，ruff 通过。
- 第二批 2/2 张真实探针成功，用时 23.92s/22.44s，每张 usage generated_images=1, output_tokens=14400, total_tokens=14400。返回模型别名 doubao-seedream-5.0-lite，日期版本未知；AFP/套餐抵扣/金额未知。
- 人工可视核对两张原图：星桥项目、林青、2026年10月15日、120万元均正确，参考图保留原文字/桥图标并增加橙色圆点；保存于 output/ark-ppt-acceptance/seedream-*.png 与 probe JSON。不将 provider 实测冒称整套 PPT 验收。
- 标准 webapp build 成功，构建期固定源校验通过；PPT 仍未开启。本批提交 feat(agent): add Seedream PPT backend，SHA 下一批记录。

## 第三批实施与验收（2026-09-12）

- 第二批提交 6b4cc16；新增 ppt_jobs/ppt_pages/ppt_events，幂等 additive migration 集成 init_db。
- 大纲/样张 revision 审批、按页 lease/heartbeat/fencing、原子预算预留、未知付费结果暂停、取消晚到结果结算、来源与 bank policy/凭据撤销校验已实现。
- 缓存绑定来源/内容/风格/backend/scope；单页变更保留其他成功页，风格变更全部失效。文件成功而数据库未提交时从 immutable attempt manifest 恢复，不重新生图。
- AFP/金额无供应商报价时不允许声称可满足该预算；token 有保守预留，未知 usage 阻止有 token 上限的后续请求。请求数硬限制始终生效。
- 隔离 PostgreSQL 11 passed，覆盖重复升级、审批、过期审批、双 worker 竞争、取消在途请求、重启复用、文件发布后恢复、未知结果 fencing、预算暂停、跨 bank、凭据/银行策略撤销、来源变化、单页/风格失效、跨任务授权缓存零新请求。所有故障测试为 mock provider，未新增付费调用。
- ruff check 通过；本批提交 feat(agent): persist PPT generation jobs，尚未启动生产 PPT worker。

## 第四批实施与验收（2026-09-12）

- 第三批提交 7b970dd。新增 create/get/approve/cancel/retry/preview PPT MCP 工具和 /api/ppt/jobs HTTP 契约；旧 generate_document 保持兼容。
- Agent 的 approve/retry 工具提供真实 UI 链接，不能替用户隐式批准；HTTP 控制携带 revision。新增 /ppt 与 /ppt/:id 页面，大纲/风格/原图映射、样张、进度、取消、单页重试与重连均读取数据库状态。
- Pi 打包 tkb-image-ppt skill，并通过 PI_AGENT_IMAGE_INPUT 显式启用图片能力。本机 Turbo 已实测视觉后设置 true；MCP 图片块一路保留至 Pi tool result，修正了 FastMCP 默认结构化包装（preview 使用 structured_output=False）。
- 预览、来源原图与下载重新检查权限并禁止公共缓存；前端不接收凭据。MCP approve 测试断言不触发 store.control，图片返回测试断言真实 image content。
- Python frontend+agent 159 passed（修复既有 test_deps 的 singleton hooks 泄漏，使反向测试顺序也通过）；Pi check 包含安全本地门禁/typecheck/105 tests/build 全通过；SPA 64 tests 与构建通过；ruff 通过。
- 本批提交 feat(webapp): review image presentation jobs。真实 UI 生图与下载验收留在第六批，不把 mock 契约当作端到端证明。

## 第五批实施与验收（2026-09-12）

- 第四批提交 ba03044。Turbo 视觉 QA 接收实际幻灯片与必需原图，严格检查 text/numbers/assets/style/layout 五项；每页最多自动修复一次，QA 与图片调用均纳入预算与 usage。
- 固定上游 create_presentation 组装整页图片与备注；独立检查图片数/摆放/16:9/备注，再用容器内 LibreOffice 与 pdftoppm 实际渲染。未通过不会发布成功下载。
- 真实无网络渲染验收：复用第二批两张已人工检查的 Seedream 图，生成 2 页 / 2 页备注，LibreOffice 25.2.3.2 520(Build:2) 成功打开；逐页查看渲染 PNG，中文、日期、金额正确，无缺图裁切。证据 output/ark-ppt-acceptance/render-probe/revision-1/，未新增生图调用。
- 单元 QA/组装 + deps 11 passed；隔离 PG 增加自动修复上限测试后 12 passed；ruff 通过。生产 worker 已有生命周期接线，但本机 PPT flag 仍 false。
- 标准镜像首次因 npm 官方 audit 临时错误失败，未绕过门禁，重试后 webapp/pi-agent 成功。图片模型实际返回 JPEG 字节，按 MIME 组装 .jpg（不凭原探针 .png 文件名判格式）。
- 本批提交 feat(agent): verify and assemble image decks。
- 浏览器技能连接失败，list()=[]；第六批优先走实际 Agent/API 入口，不声称已完成浏览器 DOM 交互验收。

## 第六批实施与部分验收（2026-09-12）

- 第五批提交 fb77aee1b。操作手册见 `docs/ark-image-ppt.md`；compose 透传 PPT 并发和页数上限。补充修正：未知 QA 显式重试复用已保存图片；重试保留视觉失败原因；区分编号必需原图与样张风格图；预算切换按钮明确取消其他额度限制。实测多图 QA 达 4465 tokens，原 4096 预留偏小，提升至 20000；这仍是估算，不冒称供应商硬报价。
- 本机已先验证 Turbo 文本链路，再启用 PPT；标准 compose build webapp/pi-agent 成功，验收镜像标记 fb77aee-ppt-acceptance-wip。最终提交后以该提交 SHA 重建并在本机 receipt 记录精确部署版本。`/health`、`/version`、`/api/graph/full`、`/api/memory/operations`、`/api/memory/facts`、`/ppt` 均 HTTP 200；Pi MCP ok，无缺失工具/schema 错误，默认记忆队列 pending/processing/failed 均 0。
- 真实 Pi Agent 会话 `01a09404-ba4b-7d09-8ba2-2f3ab12fbe3e` 读取打包 skill，调用 create_ppt，原样保存 3 页大纲并返回审核链接。任务 `411f53a6-c4b0-4004-950e-9bad204094e1` 使用独立 bank `ark-ppt-e2e`、真实已生成参考图及实际大纲/样张 HTTP 审批；首张样张人工查看正确。未使用浏览器 DOM，运行时无可用浏览器。
- **6.3 尚未完成**：总计 6/6 次真实生图、6 次真实 Turbo QA；生图 86400 tokens，QA 23977 tokens，合计 110377，held=0。该数字只覆盖 PPT worker，不包含 Agent/自动对话记忆调用；套餐抵扣、AFP、货币费用未知。第一页 accepted；第二页经修复和明确白底原图版式后，仍有标题丢失、原图重绘或多出信息框，最终 failed/visual_check_failed；第三页 pending。最终下载 HTTP 409，未生成或冒称三页成品，没有扩充预算。
- 参考图输入 API 可用，但不能可靠原样保留素材。后续设计建议已写入操作手册：原图按已审核坐标由程序等比嵌入，模型生成其他区域，之后检查最终合成页。此设计调整及新一轮生图额度待用户确认，未擅自实现或降低验收标准。
- **6.4 通过**：首张 accepted 后断开客户端并 restart webapp，hash 和 image_attempts=1 不变；新合成任务复用样张 image_attempts=0，改单页讲稿保留原样张，旧 revision HTTP 409。tokens=1 的任务 paused_budget 且零请求；两测试任务取消后保持 cancelled、无 artifact。跨 scope 状态/原图/预览/下载均 403，失败任务下载 409。真实证据 e2e-restart.json / e2e-recovery-controls.json；租约 fencing、在途取消和文件落盘恢复另有 mock 故障集成测试。
- 将部署前 dump 恢复到新的独立 `tkb_ark_ppt_baseline`，与当前数据库逐行规范化 JSON 比对：原有 documents 12、chunks 14、memory_units 205、file_summaries 2 完全一致。新验收 bank 单独有 2 条 documents（参考图和真实 Agent 对话）、22 条 memory_units，未进入默认业务 bank。指纹算法重新统一计算，详见 data.final-comparison.json，不能与早期另一种序列化 hash 直接比较。
- 验证：ruff 通过；Python 481 passed / 52 skipped，Hindsight 204 passed；隔离 PostgreSQL 13 passed；Pi 安全/typecheck/105 tests/build 通过；SPA 64 tests/build 通过；OpenSpec strict validate 通过。首次 Python 使用默认系统临时目录遇到权限错误，改用工作区 basetemp 后通过；跳过包括需额外环境的 integration 与 Windows symlink，保留既有 Starlette 弃用、Windows SSE 清理和 Vite 大包警告。
- 第六批提交 `docs(agent): record Ark PPT acceptance` 只记录通过部分与失败证据，不将 6.3 勾为完成。当前功能分支 `feat/ppt-skill-upd`；未把原有 `.gitignore` 和三个 Hindsight 换行改动纳入提交，未提交 env、output、密钥或私有素材。

## 第六批审计索引（历史）

| 批次 | 提交 | 验收依据 |
| --- | --- | --- |
| 1 | f434fd40b | memory-chain.json、probe-*.json、data.before/after-batch1.json |
| 2 | 6b4cc162c | seedream-probe-1/2.json、固定 manifest、build-batch2.log |
| 3 | 7b970ddc5 | PostgreSQL 租约/缓存/预算/权限集成测试 |
| 4 | ba030441e | MCP/BFF/前端/Pi 契约测试 |
| 5 | fb77aee1b | render-probe/revision-1/validation.json、两页真实渲染图 |
| 6 | 37f7c1dbe | e2e-final.json、e2e-events.json、e2e-recovery-controls.json、data.final-comparison.json、最终测试与 build 日志 |

私有证据统一位于 `output/ark-ppt-acceptance/`（Git 忽略）；第六批确切 SHA、六个待推送提交和部署 `/version` 保存为提交后生成的 acceptance-receipt.json。本轮交付本地待推送清单，不推送、不创建 PR、不自动合并。第六批结束时 6.3 保持开放；后续完成情况见补充批次，未归档 change。


## 第七批：已授权的原图保真合成与完整交付（2026-09-12）

- 用户确认程序原样嵌入原图的方案，并明确授权新一轮最多 6 次生图。proposal/design/ppt-generation spec 同步更新，原六批失败记录和旧任务预算保持不变。
- 实现 `reference-composite-v1`：审批页面显示固定原图区域；原图按顺序等比居中、无裁切合成，记录原图 hash/实际矩形/缩放后像素 hash。生成背景、最终 PNG 和 result manifest 独立保存；恢复、QA 和 PPTX 组装进行像素与文件校验。区域策略进入 backend/cache 身份，旧策略审批与执行均拒绝，必须 revision 更新。透明原图采用白底，遵循 EXIF 方向。
- 固定上游源未修改。适配层禁用可选有损 JPEG 压缩，并直接验证 PPTX 内嵌图片字节与最终 PNG 一致；随机噪声大于 2MB fixture 也通过，避免只用小纯色图掩盖压缩问题。
- 真实 Agent 新会话 `01a09423-2adf-7568-90c1-d2762a1941e5` 创建任务 `6200a5b7-eb0b-41ae-92be-9072879e814e`，独立 6 次请求上限。首张样张确认后 restart webapp，image_attempts=1 和样张 hash 均不变，再批准剩余页。
- 本轮前两次第二页尝试受样张布局影响，原图像素完整但遮挡了生成文字，被 QA 拦截；改用无图片输入的固定上下条带提示后，仍有多行页脚重叠，继续拦截。最终将要点明确为底边单行，用实际第 5 次生图完成第二页，第 6 次完成第三页；未降低 QA 标准、未重置预算、未额外生图。最终生成器对原图页只接收文字风格和标题/单行要点，其他页面可接收样张风格参考；QA 始终检查最终图、原图及样张。
- **6.3 / 7.3 已通过**：三页均 accepted，任务 completed、下载 HTTP 200。逐页查看 LibreOffice 25.2.3.2 520(Build:2) 实际渲染 PNG，标题、中文、2026年10月15日、120万元正确，原图与页外文字无裁切/遮挡，三页讲稿准确。PPTX 内三张图片与下载 PNG 字节完全一致；原图嵌入实际矩形 [512,288,1536,864]，缩放后像素核对通过。成品 SHA-256 `365f581fbac926d20506d570b2ae445c3f03e33cb728031138b7a01bd1a59a2e`。
- 真实 worker 用量：6/6 次生图，86400 tokens；6 次 Turbo QA，23046 tokens；合计 109446，held=0，无未知 usage。这里只统计 PPT worker；Agent/对话记忆另计，AFP、套餐抵扣、货币费用仍未知。所有失败与成功尝试保留 request ID 和 usage，不把失败消耗扣除。
- 缓存/预算/取消/权限真实 API 检查再次通过；整套三页复用以 tokens=1 阻止意外付费回退，最终 completed，三页字节一致，image_attempts=0、qa_attempts=0。改动单页保留其他成功页、旧 revision=409、越权=403、取消无假完成。scope 基线逐行比对仍为原 documents 12/chunks 14/memory_units 205/file_summaries 2 完全一致；合成 bank 的新增数据单列，不修改原有业务记录。
- 检查：ruff、OpenSpec strict 通过；Python 485 passed / 54 skipped；PPT PostgreSQL 15 passed；Pi 105 tests/typecheck/security/build 通过；SPA 64 tests/build 通过。54 skips 包含默认不运行的外部集成测试及 Windows symlink；保留既有 Starlette/Windows SSE 清理/Vite chunk 警告。新增数据库测试最初期望晚于实际审批门禁的拒绝，修正为同时覆盖审批和执行两个拒绝阶段后通过。
- 标准 Docker 构建完成并部署验收版本；最终提交后再以准确 SHA 构建和核对 `/version`。新增代码、规划和验收记录作为补充批次提交当前 `feat/ppt-skill-upd`，不纳入原有 `.gitignore`/Hindsight 换行修改，不推送、不自动合并、不归档。
- 私有证据目录 `output/ark-ppt-acceptance/composite/`：presentation.pptx、state.json、events.json、integrity.json、restart.json、full-cache.json、e2e-recovery-controls.json、data-comparison.json 和 artifacts/revision-1/rendered。最终提交与部署回执存 acceptance-receipt.json；这一批完成后全部 33 项任务完成。

- 实际 Pi 图片链路补验：同一真实会话只调用一次 tkb_preview_ppt 查看第二页，回答白底、页脚单行横排、标题和120万元均与图像一致；证据 agent-preview-events.txt。该检查只读取既有图片，未调用生图。浏览器 DOM 仍未验收（无可用浏览器），真实 Agent/API 与逐页渲染验收已覆盖本变更要求的实际入口。
