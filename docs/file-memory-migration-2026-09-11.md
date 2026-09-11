# 历史文件摘要迁移验收（2026-09-11）

本机 `default-team` 的 1 份历史 PDF 已完成清理和摘要重处理。运行 `8f3854e9-eff0-4ec7-b479-4653d878d5d0` 状态为 `verified`。页面 `/api/memory/facts?limit=500` 实测返回 108 条，迁移前为 361 条。

| 页面可见条目（不计 source chunk） | 迁移前 | 迁移后 |
|---|---:|---:|
| 文件 facts | 258 | 51 |
| 文件依赖 observations，含混合来源 | 57 | 11 |
| 对话 facts | 33 | 33 |
| 不依赖目标文件的对话 observations | 13 | 13 |
| 合计 | 361 | 108 |

57 条旧 observation 的当前正文已清空、状态 retired，审计历史保留。旧全文 memory active/stale 数、有效旧 evidence 数、旧图节点数均为 0；所有新 observation 的来源均是当前有效事实。旧 ID 的权威 cache 校验返回空，旧内容不会重新进入提示。

原文和 14 个原始向量分块逐项校验不变，非目标记忆指纹不变。最终队列：consolidation 1 completed、conversation 10 completed、mental-model refresh 1 completed、graph outbox 325 completed；失败和待处理为 0。

## 查询验收

- 默认查询现已接通原始文件向量，并以有界关键词候选补充精确名称/数字；这些片段可引用、展开，不写入 facts，也不进入 fact cache。bank、文档和 chunk 标签、状态、来源、时间过滤继续生效。
- 文件尾部问题：“SDK 全线协议兼容、全部第三方集成和可选搜索后端是否属于当前核心对齐范围？”在实际 `/api/search` fast 查询 Top-5 中找到原文。单纯向量 Top-5 未命中这个精确边界，因此补齐了关键词分支，未把未命中的测试报告为成功。
- 实际 PostgreSQL 对话事实 + 固定 planner 的对照：冷查询检索 1 次、planner 2 次；热查询新增检索 0 次、planner 1 次；cache hits=1，答案和引用有效。固定 planner 用于隔离缓存效果，此项没有生成 API 调用，不代表线上所有问题都有同样收益。
- 迁移及对照日志、完整响应与备份位于非提交的 `output/`，此报告不包含原文、对话内容、凭据或恢复载荷。

## 调用与预算

迁移累计 14 次生成调用，13 次取得 provider usage，合计 41,677 tokens：摘要 8,000、fact 抽取 6,713、consolidation 26,964。首次摘要失败未取得 usage，因此不能把 41,677 当作完整账单总量。

摘要尝试 2 次，其中成功 1 次；成功摘要在后续所有续跑中复用，重复成功摘要调用为 0。fact 抽取成功后也未重做。数据库预算账本累计计入 85,024 tokens，其中已结算成功调用为 36,204，其余为失败调用保守预留；截断 consolidation 的 5,473 tokens 在外部执行日志中可见，但仍保留原预留。始终未提高 100,000 的运行额度。未配置价格，账本中的 cost=0 不代表免费；embedding、查询和独立后台刷新不计入上述迁移生成用量，不宣称账单节省百分比。

实际运行暴露并修复了同名不同 ID 实体的图投影误拒、结构化输出截断和删除事件重复综合。DeepSeek V4/V3.2 的有界 JSON 请求显式关闭思考模式；这使用其[官方思考模式参数](https://api-docs.deepseek.com/guides/thinking_mode/)。摘要模板身份升为 `bounded-overview-v2`，不同策略不误用旧摘要。

迁移 worker 每批读取 64 个事件，最多输出 4 个 actions，consolidation 输出上限 4,096 tokens；事件批量与输出条数分开。已确认 evidence 全部失效的旧删除事件只推进状态，不重复调用模型。

## 部署、恢复与追溯

用户明确回复“授权”，允许本机版本切换，作为本次 Windows Docker 对 `CLAUDE.md` 手动 compose 限制的明确例外。没有 push、merge 或改动其他服务配置。最终代码镜像为 `team-kb-webapp:c5d410e8`，使用已核对相同依赖和前端输入的本地依赖层构建，未混入工作区无关改动。

目标环境为 `localhost:5433/knowledge_base`、schema `public`、bank `default-team`。恢复文件 `output/rebuild-recovery-8f3854e9-eff0-4ec7-b479-4653d878d5d0.json` 的 checksum 为 `243fca393d9c9f49eaa13d2b4eeaabe3619286b83fbfe7dd04da21f5b677c799`；已用目标实际备份在独立临时 PostgreSQL 验证退休后精确恢复。目标后来已完成新写入，因此不能再用 `restore` 覆盖回旧状态，应前向修复。

可重复只读核验：

```powershell
uv run python -m src.engine.hindsight_components.file_rebuild_runner verify --bank default-team --run-id 8f3854e9-eff0-4ec7-b479-4653d878d5d0
```

原文 checksum：`65167f692d517a8948b6068ae03e8c025dcfdb57f42270081b59ae50edf7bf7d`；向量分块 checksum：`e843f26dc62dc11ac0471ef639bae959ed6d6e000c17eae0931354188187d663`。后续用户正常编辑会改变这些基线，不应以旧备份覆盖新编辑。
