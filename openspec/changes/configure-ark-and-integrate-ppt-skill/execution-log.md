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
