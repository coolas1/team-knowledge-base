## 1. 第一批：方舟文本配置与真实记忆验收

- [x] 1.1 从最新 develop 建立功能分支（沿用用户已建立的 feat/ppt-skill-upd），记录工作区、容器、数据库及模型基线；验收：不纳入原有换行改动、output 或密钥，保存实时数据指纹和恢复材料。
- [x] 1.2 添加用途分离的记忆思考配置及能力校验，覆盖摘要、retain、consolidation、mental-model refresh，策略纳入缓存身份；验收：请求契约测试覆盖豆包 disabled、DeepSeek 兼容、未知模型拒绝及旧缓存不误用。
- [x] 1.3 核对用户当前 Turbo/Agent Plan 配置，清理无效 LLM_PROVIDER，透传所需设置、构建镜像并重新创建容器实例；验收：webapp 与 Pi 有效 endpoint/model 均为方舟，key 不输出，embedding 与现有数据不变。
- [x] 1.4 在合计 10000 输出 tokens / 12 次生成请求上限内验证 JSON、stream、tool call、图片输入和关闭思考，再在隔离 bank 验证摘要→facts→observation→引用查询；验收：记录真实 usage/实际版本/失败详情，关闭思考无隐藏推理消耗或明确标注无法核实，套餐抵扣未核实则不宣称已确认。
- [x] 1.5 运行配置、provider、记忆缓存及相关全库检查，提交 feat(engine): configure Ark memory reasoning；验收：tests/ruff 通过，execution-log 记录 SHA、预算和容器验收证据，第一批不启用 PPT。

## 2. 第二批：固定 PPT skill 与 Seedream 适配

- [x] 2.1 读取并固定上游 f47bd3e54e49d14d51807692694e2d5619a8e298 的必要 skill/scripts/references，保留 license 和 manifest，记录串行 worker 替代子 agent 等差异；验收：固定文件 checksum 可复核，构建不下载浮动 main。
- [x] 2.2 添加独立 IMAGE_* 和 PPT 开关配置；验收：缺少模型/key、未知 provider、错误计费路由明确报错，文本和图片凭据不隐式互用，不影响旧文本接口。
- [x] 2.3 实现 Seedream 文生图/参考图 provider 及受限下载，按实际模型能力映射尺寸、格式等参数；验收：契约覆盖 URL/base64、重定向/超限拒绝、429/超时、缺失图片和不兼容 GPT 参数。
- [x] 2.4 使用专门配置的套餐图片 key 验证候选 Seedream 名称，最多生成 2 张合成图（文生图和参考图）；验收：实际图像可打开、参考内容可核对，记录耗时/request id/usage/套餐证据，权限或能力不足时明确保持未完成。
- [x] 2.5 执行 provider 测试与标准镜像构建检查，提交 feat(agent): add Seedream PPT backend；验收：上游归属、适配说明、模型能力报告和本批 SHA 齐全。

## 3. 第三批：持久化 PPT 任务、预算和续跑

- [ ] 3.1 添加 job/page/event 的幂等数据库升级与 artifacts 卷目录，保存 scope/revision/审批/模型和页面身份；验收：隔离 PostgreSQL 新建及重复升级成功，不修改现有业务记录。
- [ ] 3.2 实现大纲批准、单样张、样张批准、生成、检查、组装状态流和快速返回任务 ID 的服务入口；验收：缺审批不产生后续生图调用，旧 revision 审批拒绝，普通聊天超时不终止持久化任务。
- [ ] 3.3 实现逐页 worker 租约、心跳与 fencing，处理成功持久化前后崩溃和未知 provider 结果；验收：重启复用成功页、旧 worker 不能覆盖，未知结果不自动重复付费请求。
- [ ] 3.4 实现 scope 内单页缓存及原子预算预留/结算、取消和受控重试；验收：同页复用零新增请求，风格/模型/来源变更失效，跨 worker 不超预算，取消后不启动新页，不明费用不记零。
- [ ] 3.5 完成隔离数据库故障注入、跨 bank/权限撤销测试并提交 feat(agent): persist PPT generation jobs；验收：测试覆盖状态与权限交叉行为，记录 SHA 和重启证据。

## 4. 第四批：Agent 工具与样张预览交互

- [ ] 4.1 增加 create/status/approve/retry/cancel PPT 工具与 HTTP 契约，保持旧 generate_document 不变；验收：MCP 与 BFF 测试验证 schema、可信 binding、错误类型和向后兼容。
- [ ] 4.2 打包本地适配 skill，接通模型图片输入和参考素材，给 Agent 清晰的图片式 PPT 路由；验收：模型实际收到图片消息而非仅文件路径，旧 DOCX/PDF/普通 PPT 路由不回退。
- [ ] 4.3 前端添加大纲/样张预览和 revision 绑定审批、进度、取消、失败页与单页重试；验收：未批准样张无法批量执行，过期批准提示更新，重连恢复真实任务状态。
- [ ] 4.4 全链路校验来源、任务、预览、缓存与下载权限，执行新前端与 Agent 契约测试并提交 feat(webapp): review image presentation jobs；验收：跨 bank 与撤销访问均拒绝，展示内容无凭据，记录 SHA。

## 5. 第五批：视觉 QA、讲稿和 PPTX 交付

- [ ] 5.1 接入逐页结构与视觉 QA，验证中文、标题、数值、参考图和统一风格；验收：明确失败 fixture 被阻止，最多一次自动修复且计入预算，不能用自检成功替代真实预览。
- [ ] 5.2 组装整页图片 PPTX、每页讲稿备注、大纲和预览，发布前验证完整性；验收：页数/顺序/画幅/备注准确，文件可由实际渲染工具打开，无缺图、裁切或空白页。
- [ ] 5.3 登记最终 artifact 并复用授权下载，明示图片页不可逐项编辑；验收：链接真实可用、越权拒绝，未完成任务不显示成功下载，不冒称 Slidev 为图片页可编辑还原。
- [ ] 5.4 执行渲染验收、附件兼容测试并提交 feat(agent): verify and assemble image decks；验收：记录渲染工具版本、预览证据和 SHA，业务私有内容不进入 Git。

## 6. 第六批：Docker 端到端验收与操作手册

- [ ] 6.1 更新环境示例与操作手册，说明模型分工、套餐路由、额度/未知结果、恢复和回退；验收：无密钥、无价格零值误导，备份针对实时基线而非历史条目数。
- [ ] 6.2 标准 build 并部署已授权本机镜像，先验证 Turbo 文本链路，再开启 PPT worker；验收：/health、/version、Pi/MCP、图和记忆队列健康，现有数据指纹符合预期。
- [ ] 6.3 从实际 UI/Agent 入口完成 3 页合成 PPT（最多 6 次生图），包含样张批准、必需参考图、文字数字核对、讲稿和最终下载；验收：逐页人工可视检查通过，记录真实生成与 QA usage，不使用 mock 图冒充验收。
- [ ] 6.4 在合成任务上验证断线重连、worker 重启、取消、预算暂停及改单页缓存，记录实际请求差异；验收：已完成页不重做，无越权或取消后假完成，所有未知/失败有明确终态。
- [ ] 6.5 运行 ruff、Python、Pi、前端测试和构建、必要 PostgreSQL 集成及 OpenSpec strict validate，提交 docs(agent): record Ark PPT acceptance；验收：仅通过后勾选，汇总六批 SHA、失败/跳过说明和部署代码版本。
- [ ] 6.6 完成最终审计索引；实施授权包含推送/PR 时创建目标 develop 的 PR，否则交付待推送提交清单；验收：PR/清单与已验证提交一致，不自动合并、不把规划完成视为实施完成。
