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
