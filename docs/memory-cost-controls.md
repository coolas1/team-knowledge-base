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

本修改不自动批量重建已有文件或删除已有 facts / observations，也不启动线上任务。新上传、编辑重新索引的文件使用摘要记忆；历史 backfill 命令仍保留原有行为。缓存主要降低后续 adaptive reflect 的重复检索和证据上下文成本，文件摘要降低新增文件的抽取与 consolidation 输入量。实际节省依赖文档长度和查询复用率，尚未以线上 DeepSeek 账单量化。

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
