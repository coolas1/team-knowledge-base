# 文件摘要和 fact 工作缓存

`config/app.yaml` 默认启用 `engine.ingest.vector_only: true`。上传和编辑重新索引时，原文仍写入 `documents.raw_text`，全部文本分块生成向量；取消逐块 LLM 实体、关系抽取。文档摘要写入原有 overview 字段，并代替全文传入 retain，随后由摘要提取的 fact 参与 observation / consolidation。对话 retain 不受此开关影响。

摘要每次索引只调用一次 LLM，输入最多约 24,000 字符，输出最多 2,000 字符。长文档均匀抽取八段（包含开头和结尾），摘要显式标记为抽样摘要；具体细节应查询原文向量索引。未配置 LLM 时使用带标记的文本摘录。文件未变化且已索引时沿用原有幂等跳过逻辑，不重新生成摘要。关闭 vector_only 可恢复原有图谱抽取流程。

## Fact 缓存

- `engine.memory.fact_cache_capacity: 256`：进程内共享工作缓存，达到容量时驱逐最久未使用的条目；设为 0 关闭缓存。
- `fact_cache_ttl_seconds: 1800`：自最近一次有效使用起 30 分钟失效，在读写缓存时清理。驱逐不删除数据库中的 fact。
- `fact_context_limit: 8`：每次缓存预加载或 observation 展开最多选择八条相关 fact。
- `fact_context_max_tokens: 1200`：上述单次证据文本的估算 token 上限；超出预算的单条 fact 跳过。缓存也不保存超过 1,200 估算 token 的单条 fact。

通过召回相关性门槛且被最终选中的 world / experience fact，以及展开时选中的来源 fact，进入缓存。缓存按完整 memory scope 和请求过滤条件隔离，随 scoped service 复用；进程重启后为空，各进程独立。

Adaptive reflect 在调用规划模型前，从缓存中按英文词和中文双字词重合度筛选相关证据。每次复用都通过 scoped repository 查询当前记录，确认仍可见、有效且内容/版本时间未变化，再把文本提供给模型；失效条目立即删除。命中不会被视为证据已经完整，模型仍可检索缺失信息。缓存查询失败会回退到常规检索。

Observation 搜索不再自动加载全部来源 fact。展开按当前问题筛选并返回 `total_source_facts` 和 `truncated`，模型可用不同的 `expand(memory_id, query)` 或 recall 补查。公共显式证据展开接口仍可用于完整审阅。

## 生效范围

上传、编辑、backfill、retry/reprocess 统一使用持久化摘要；相同原文、模板、模型和预算身份复用成功摘要。对话保持原有抽取。历史数据通过下面的显式迁移运行清理和重建，普通服务启动不会自动删除旧记忆。实际费用需要 provider usage 和账单核验，测试中的调用减少不能当成线上节省率。

## 历史文件迁移工具（分批执行）

迁移 schema 由 `init_db` 的幂等升级建立。下面命令均在已核对连接配置的目标环境运行，bank 必填；运行凭据与备份不提交 Git。

```powershell
uv run python -m src.engine.hindsight_components.file_rebuild preview --bank BANK --output manifest.json
uv run python -m src.engine.hindsight_components.file_rebuild plan --bank BANK --manifest manifest.json --backup recovery.json
uv run python -m src.engine.hindsight_components.file_rebuild retire --bank BANK --run-id RUN_ID
uv run python -m src.engine.hindsight_components.file_rebuild restore --bank BANK --run-id RUN_ID --backup recovery.json
```

`preview` 不写数据库、不调用生成模型，报告来源不明的文件；可以重复 `--document-id` 限定批次。`plan` 核验数据库身份、内容 hash、revision 和完整证据关系，并导出带 checksum 的恢复材料。未导出恢复材料时禁止 retire。

`retire` 清理当前 observation 内容、旧事实有效性及抽取快照，保存审计历史，关联 mental models 失效并排队 graph replace。图投影只写 active memories 和 active 链接目标。重复 retire 幂等。

`restore` 仅用于清理后、尚无后续写入的运行；校验恢复文件 checksum、运行身份、当前行指纹与原文 hash，发生后续写入时拒绝恢复。清理成功并不代表重建完成，须继续摘要 retain / consolidation 并核验图队列终态。


## 摘要重建、预算和续跑

完成 `preview` 和 `plan` 并保存恢复文件后，推荐直接执行 `resume`：它先准备成功摘要，再退休旧内容，每次默认串行 retain 一个文件。每次返回后使用相同 run ID 继续，直到 `verified`。不要把 `retaining` 或 `awaiting_projection_or_consolidation` 当成完成。

```powershell
uv run python -m src.engine.hindsight_components.file_rebuild_runner resume --bank BANK --run-id RUN_ID --batch-size 1 --max-tokens 100000
uv run python -m src.engine.hindsight_components.file_rebuild_runner verify --bank BANK --run-id RUN_ID
```

`--max-tokens` 是整个 run 的累计额度，不是每次续跑重新分配。`--max-cost-usd` 配合 `--input-price`、`--output-price`（美元/百万 token）限制生成调用费用。预算覆盖摘要、fact 抽取和 consolidation；向量 embedding 调用未纳入该生成模型预算。每次调用前预留保守上限，实际 usage 到达后结算；没有 usage 或调用中断时保留预留，不宣称是实际账单。`budget_paused` 可通过提高同一个 run 的预算后续跑，成功摘要不会重复生成。

运行拒绝源内容/revision/摘要策略变化。已有新写入时采用前向修复，禁止把旧备份覆盖回去。`awaiting_unrelated_consolidation` 要求当前范围的其他来源队列先完成；迁移不会吞掉那些事件。不同 scope 的任务不由本次迁移领取。

迁移前确认所有读写 worker 使用相同的新版本；旧容器不会因为当前分支有提交而自动更新。按仓库部署流程升级后，重新生成并核对 manifest 和备份，再运行迁移。目标备份、原文和凭据保存在 `output/` 等非提交位置。
