# 配置参考

本文是全部配置项的唯一详细参考。配置分两处：

- **`.env`**(从 `.env.example` 复制,已 gitignore)— 基础设施连接与模型端点。
  `docker-compose.yml` 通过 `${VAR}` 替换读取它;应用本身经
  `config/settings.py`(pydantic-settings)读取。
- **`config/app.yaml`** — 引擎行为(摄取并行度、记忆、MCP 工具负载上限)。

各配置文件内只保留一行提示并指向本文。表中"来源"列说明该变量定义于何处:

- `.env.example` — 模板中列出(部分为注释掉的可选项)
- `config/settings.py` — 仅有代码默认值,未列入 `.env.example`,需要时可在 `.env` 中覆盖
- `docker-compose.yml` — compose 自带默认值,通常无需在 `.env` 设置

---

## 配置分层与覆盖

### 三层，逐键高者胜

有效配置由三层解析而成：

| 层 | 位置 | 谁来写 |
|---|---|---|
| 已提交默认值 | `config/app.yaml` | 维护者,随代码提交;运行中的应用**绝不**写它 |
| 环境覆盖 | `.env` 与进程环境变量(派生名 `TKB_*`) | 每个部署自己 |
| 运行时改动 | `config/app.runtime.yaml` | `PUT /api/config` |

环境层读两处:进程环境变量与工作目录下的 `.env`,两者写派生名都生效,
**进程环境变量优先**(与 `pydantic-settings` 的 env > .env 一致)。`.env` 缺席
时这一层就只是进程环境——与引入分层之前完全一致。

层缺席即不参与：没有运行时文件、也没设环境变量的部署,解析结果恰好等于已提交的
默认值。`GET /api/config` 返回有效配置,并逐键给出 `sources`(`default`、
`app.yaml`、`env`、`runtime`),所以"我改了但没生效"能看出是哪一层压住了它。

### 派生覆盖名

`config/app.yaml` 里**每一个**叶子键都可以用环境变量覆盖,名字由路径派生：

```
TKB_ + 点分路径的大写、下划线连接
```

例：`engine.ingest.chunk_concurrency` → `TKB_ENGINE_INGEST_CHUNK_CONCURRENCY`;
`engine.memory.consolidation_batch_size` → `TKB_ENGINE_MEMORY_CONSOLIDATION_BATCH_SIZE`。

名字只由已存在的 schema 路径**生成**、再去环境里查存在与否,永不反解析回路径,
因此键名里的下划线(`chunk_concurrency`)与路径分隔符不会混淆。新增配置键自动
获得覆盖名,不需要额外代码;改某个部署的行为不再需要改已提交文件或重建镜像。
类型转换交给 schema 的 pydantic 类型,值不合法时的报错与写在文件里一致。

### 别名：`ARCHIVE_*`

`ARCHIVE_*` 是唯一早于派生规则、且命名对应 `config/app.yaml` 键的前缀族。两个
在线部署都在用它,所以保留为**有文档的别名**而不是改名。同一旋钮同时设置时
优先级为：

```
ARCHIVE_*  >  TKB_ARCHIVE_*  >  config/app.yaml
```

`ARCHIVE_WORKSPACE_DIR` 是部署路径,`app.yaml` 里没有对应键,只来自环境,不受
别名规则影响。`HINDSIGHT_*` 与 `ENGINE_TOOLS_*` **不是**别名:它们是
`config/settings.py` 的原生字段,今天就从环境读取,与 `app.yaml` 的重名项如何
合并见下方"暂缓项"。

### `.env.example` 只列部署要选的事实

模板只放凭据、端点、宿主端口、路径、功能开关;行为默认值留在 `config/app.yaml`
(需要按部署改时用上面的 `TKB_*` 名,不必回填模板)。两个测试守着这条线:模板里
每个键都必须有人读,每个部署必需项都必须列在模板里。

### 暂缓项(本次不做)

- **旋钮合并**：把 `HINDSIGHT_*` / `ENGINE_TOOLS_*` 与 `config/app.yaml` 里
  描述同一件事的段落(如 `hindsight_graph_worker_enabled` 对 `memory.graph_worker`)
  收敛到一处。这是后续独立改动,本机制先跑通。
- **pi-agent sidecar**：`src/extensions/pi-agent` 的约 50 个 `PI_AGENT_*` /
  `TKB_*` 键采用同一套约定,同样另开一个改动。
- **运行时覆盖的持久化**：`config/` 被烤进镜像,`/app/config` 不是 compose 挂载,
  所以运行时改动只在容器重建之前有效。这是既有缺陷(`PUT /api/config` 一直如此),
  分层已按最终形态命名与排序,日后加挂载只需改文件位置,不动优先级语义。
- **让部署的 `.env` 直达容器**：compose 的 `environment:` 是显式白名单,
  `--env-file deploy.env` 只喂 `${VAR}` 插值,不进容器环境。因此通用 `TKB_*`
  覆盖在宿主运行(CLI / MCP / `uv run`)与容器内都可用,但在 compose 部署里
  还需要一步:把部署侧的 `deploy.env` 改名为 `.env` 并为服务加 `env_file: .env`。
  这要迁移宿主机上的 `.deploy/*/deploy.env`(见 cicd/README.md)并重新部署,
  单独一个改动跟进。

---

## 数据库与存储

### Postgres(pgvector,由 docker compose 管理)

| 变量 | 默认值 | 说明 |
|---|---|---|
| `POSTGRES_HOST` | `localhost` | 主机 |
| `POSTGRES_PORT` | `5433` | **宿主机**发布端口(容器内始终监听 5432) |
| `POSTGRES_DB` | `knowledge_base` | 库名 |
| `POSTGRES_USER` | `kb_user` | 用户 |
| `POSTGRES_PASSWORD` | — | 密码 |

### Neo4j(由 docker compose 管理)

| 变量 | 默认值 | 说明 |
|---|---|---|
| `NEO4J_URI` | `bolt://localhost:7687` | 连接 URI |
| `NEO4J_USER` | `neo4j` | 用户 |
| `NEO4J_PASSWORD` | — | 密码 |
| `NEO4J_BOLT_PORT` | `7687` | 宿主机发布端口,**必须与 `NEO4J_URI` 中的端口一致** |
| `NEO4J_HTTP_PORT` | `7474` | 宿主机发布的 HTTP 端口 |

### 上传

| 变量 | 默认值 | 说明 |
|---|---|---|
| `UPLOADS_DIR` | `uploads` | 上传原件存放目录。compose 部署会覆盖为绝对卷挂载 `/app/uploads`,使上传在容器重建后保留;相对默认值用于宿主机开发 |
| `KB_MAX_UPLOAD_BYTES` | `104857600`(100 MiB) | 单文件上传上限(字节),单/批量上传均适用。超限返回 413 `file_too_large`,且读取有界——超限部分不会被缓冲 |

### 运行时目录

| 变量 | 默认值 | 说明 |
|---|---|---|
| `HOME` | `./team-kb-runtime` | 挂入后端容器的 HuggingFace 缓存目录。默认 reranker 关闭,无需本地 HF 模型;请用项目相对运行时目录而非用户专属路径 |

### 应用服务

| 变量 | 默认值 | 说明 |
|---|---|---|
| `APP_HOST` | `0.0.0.0` | BFF 监听地址 |
| `APP_PORT` | `8000` | BFF 监听端口 |

---

## 模型端点

所有端点均为 OpenAI 兼容协议。

### Embedding(`/v1/embeddings`,无开关——搜索依赖它)

| 变量 | 默认值 | 说明 |
|---|---|---|
| `EMBEDDING_BASE_URL` | `http://localhost:11434/v1` | 任意 OpenAI 兼容端点。本地 Ollama:`http://localhost:11434/v1`;compose `--profile ollama` 部署内:`http://ollama:11434/v1` |
| `EMBEDDING_MODEL` | `nomic-embed-text` | 模型名。**必须输出 768 维向量**——存储宽度固定,其他宽度 Embedder 会快速失败 |
| `EMBEDDING_API_KEY` | 空 | API key(Ollama 无需) |

### 对话/分析 LLM(`/chat/completions`,用于文档分析(overview/entities)与 ask/summarize)

| 变量 | 默认值 | 说明 |
|---|---|---|
| `LLM_MODEL` | `deepseek-v4-flash` | 模型名 |
| `LLM_BASE_URL` | `https://api.deepseek.com/v1` | 任意 OpenAI 兼容 API;**置空即禁用 LLM**。本地 Ollama:`LLM_BASE_URL=http://ollama:11434/v1`、`LLM_API_KEY=ollama` |
| `LLM_API_KEY` | — | API key |
| `LLM_MEMORY_THINKING` | `auto` | `auto` / `disabled` / `enabled`,仅作用于后台记忆;显式策略需要端点/模型支持。Ark Agent Plan 示例:doubao-seed-2.1-turbo @ `https://ark.cn-beijing.volces.com/api/plan/v3`,`LLM_MEMORY_THINKING=disabled` |

### 图像生成(独立显式配置的凭据,不回退复用 LLM 凭据)

| 变量 | 默认值 | 说明 |
|---|---|---|
| `IMAGE_PROVIDER` | `ark` | 目前仅支持 `ark` |
| `IMAGE_BASE_URL` | 空 | 必须是已验证的 Ark Agent Plan 路由 `https://ark.cn-beijing.volces.com/api/plan/v3` |
| `IMAGE_MODEL` | 空 | 必须是已验证 Seedream 能力档位的 `doubao-seedream-5.0-lite` |
| `IMAGE_API_KEY` | 空 | Ark API key |

### PPT / 图像输入

| 变量 | 默认值 | 说明 |
|---|---|---|
| `PPT_ENABLED` | `false` | 启用图像演示(PPT)生成 |
| `PPT_MAX_PAGES` | `20` | 单次生成页数上限(1–20) |
| `PPT_DEFAULT_PAGES`(settings.py) | `8` | 默认生成页数(1–20) |
| `PI_AGENT_IMAGE_INPUT` | `false` | 允许 Agent 接收图像输入 |

---

## 检索

### Reranker(搜索守门人)

| 变量 | 默认值 | 说明 |
|---|---|---|
| `RERANKER_PROVIDER` | `none` | `local` / `http` / `none`,详见下 |
| `RERANKER_MODEL` | `BAAI/bge-reranker-v2-m3` | 模型名 |
| `RERANKER_BASE_URL` | 空 | `http` 模式下的 API 根;代码 POST `{base_url}/rerank` |
| `RERANKER_API_KEY` | 空 | `http` 模式 API key |

provider 说明:

- **`local`** — CrossEncoder,需要 `uv sync --extra reranker`(torch),且 BAAI/bge-reranker-v2-m3 模型已缓存于 `$HOME/.cache/huggingface`(见上文 `HOME`)。
- **`http`** — 外部 `/v1/rerank` API(Cohere/Jina/OpenAI 兼容),无需 torch。`RERANKER_BASE_URL` 是 API 根,代码 POST `{base_url}/rerank`。示例 Jina:`base_url=https://api.jina.ai/v1`、`model=jina-reranker-v2-base-multilingual`。
- **`none`** — 关闭(仅向量 top-K,不做 rerank)。

### 深度检索时限(Hindsight deep search)

| 变量 | 默认值 | 说明 |
|---|---|---|
| `HINDSIGHT_DEEP_TOTAL_TIMEOUT_SECONDS` | `45` | deep 检索总单调时限 |
| `HINDSIGHT_QUERY_ANALYSIS_TIMEOUT_SECONDS` | `8` | 查询分析阶段时限 |
| `HINDSIGHT_QUERY_EMBEDDING_TIMEOUT_SECONDS` | `10` | 查询向量化阶段时限 |
| `HINDSIGHT_RETRIEVAL_ARM_TIMEOUT_SECONDS` | `5` | 单条检索臂时限 |
| `HINDSIGHT_RERANK_TIMEOUT_SECONDS` | `12` | rerank 阶段时限 |
| `HINDSIGHT_RERANK_CANDIDATE_LIMIT` | `40` | rerank 候选条数上限 |
| `HINDSIGHT_RERANK_TEXT_LIMIT_CHARS` | `4000` | 单条 rerank 文本字符上限 |
| `HINDSIGHT_RERANK_TOTAL_CHARS` | `60000` | rerank 总字符预算 |
| `HINDSIGHT_KEYWORD_CANDIDATE_LIMIT` | `300` | Python BM25 关键词候选上限 |
| `HINDSIGHT_KEYWORD_INDEX_ENABLED` | `false` | **仅在词法回填报告 `complete=true` 后启用** |

### 召回相关性门槛

公开搜索/应答召回:语义下限在所有模式生效,deep 模式的 rerank 分数门槛叠加其上。低于门槛的结果被丢弃,使无覆盖的查询返回"未找到"而非无关记忆。

| 变量 | 默认值 | 说明 |
|---|---|---|
| `HINDSIGHT_RECALL_MIN_SEMANTIC` | `0.45` | 语义相似度下限(所有模式) |
| `HINDSIGHT_RECALL_MIN_SCORE` | `0.4` | deep 模式 rerank 分数下限 |
| `HINDSIGHT_CONVERSATION_RECALL_MIN_SEMANTIC` | `0.25` | 会话记忆召回使用独立的更低语义下限——收紧公开门槛不影响召回哪些记忆 |
| `HINDSIGHT_RECALL_MIN_TERM_COVERAGE`(settings.py) | `0.5` | 关键词臂逃生口:仅靠关键词匹配的候选,至少要覆盖查询显著词的这一比例 |
| `HINDSIGHT_RECALL_MIN_TERM_COUNT`(settings.py) | `2` | 且至少命中这么多个词,才能通过相关性门槛 |
| `HINDSIGHT_RERANK_SEMANTIC_MARGIN`(settings.py) | `0.25` | 用向量相似度封顶神经 rerank 分数,阻止 LLM 为语义无关的 chunk"幻觉"出高分 |

---

## 记忆

### Neo4j 投影 worker

PostgreSQL 始终是权威存储;当前部署同时维护一次性的 Neo4j 记忆投影。

| 变量 | 默认值 | 说明 |
|---|---|---|
| `HINDSIGHT_GRAPH_WORKER_ENABLED` | `true` | 置 `false` 关闭该投影(无 Neo4j 部署的杀开关;主开关是 app.yaml 的 `engine.memory.graph_worker`) |
| `HINDSIGHT_GRAPH_WORKER_POLL_SECONDS` | `1` | 轮询间隔 |
| `HINDSIGHT_GRAPH_WORKER_LEASE_SECONDS` | `300` | 任务租约时长 |
| `HINDSIGHT_GRAPH_WORKER_MAX_ATTEMPTS` | `10` | 最大尝试次数 |

### 会话记忆(共享团队范围;留存经队列、最终一致)

| 变量 | 默认值 | 说明 |
|---|---|---|
| `HINDSIGHT_CONVERSATION_MEMORY_ENABLED` | `true` | 启用自动会话记忆 |
| `HINDSIGHT_CONVERSATION_RECALL_LIMIT` | `20` | 召回条数上限(1–100) |
| `HINDSIGHT_CONVERSATION_MAX_TURN_CHARS` | `100000` | 单轮留存内容上限(字符):超出截断并附 `[truncated]` 标记,避免一次粘贴的巨文档扇出数十次 LLM 抽取 |
| `HINDSIGHT_CONVERSATION_WORKER_POLL_SECONDS` | `1` | worker 轮询间隔 |
| `HINDSIGHT_CONVERSATION_WORKER_LEASE_SECONDS` | `300` | 任务租约时长 |
| `HINDSIGHT_CONVERSATION_WORKER_MAX_ATTEMPTS` | `10` | 最大尝试次数 |
| `HINDSIGHT_CONVERSATION_WORKER_MAX_CONCURRENT` | `1` | worker 并发数 |
| `HINDSIGHT_CONVERSATION_WORKER_RETRY_SECONDS` | `1` | 重试初始间隔 |
| `HINDSIGHT_CONVERSATION_WORKER_MAX_RETRY_SECONDS` | `300` | 重试间隔上限 |
| `HINDSIGHT_CONVERSATION_RETENTION_CONTEXT` | `Completed team conversation turn` | 留存记录的上下文说明 |

---

## Pi Agent

### 运行方式与 BFF 代理

普通 `docker compose up` 会连同后端一起启动 Pi Agent;compose 将容器内 BFF URL 固定为
`http://pi-agent:8010`。宿主机 BFF 开发可设置 `PI_AGENT_URL=http://localhost:8010`(BFF 代理目标,来源:routes_agent.py)。

| 变量 | 默认值 | 说明 |
|---|---|---|
| `PI_AGENT_READ_TIMEOUT_SECONDS` | `30`(未设置时) | 非流式 `/api/agent/*` 代理路由(会话列表/详情/创建/删除/取消、memory-forget)的读时限。接受连接但不应答的 sidecar 会变成 504 而非无限挂起。SSE 消息路由**不受此限**(长模型回答不能被拦腰截断),保持 read=None 但连接/写入/池有界。设 0 或负数关闭该上限。**必须低于 SPA 客户端截止时间**(编译期常量 45s,见 `src/frontend/webapp/client/src/api/client.ts`),否则客户端先中断,看不到服务器更具体的错误 |

### 运行时参数(compose 供应默认值;显式 `PI_AGENT_*` 优先,未设置时继承共享 `LLM_*`,否则回退本地 Ollama 默认)

| 变量 | 默认值 | 说明 |
|---|---|---|
| `PI_AGENT_PORT` | `8010` | 服务端口 |
| `PI_AGENT_API` | `openai-completions` | API 协议 |
| `PI_AGENT_PROVIDER` / `PI_AGENT_MODEL` / `PI_AGENT_MODEL_NAME` | 空 | 显式指定 provider/模型(覆盖 LLM_* 继承) |
| `PI_AGENT_BASE_URL` / `PI_AGENT_API_KEY` | 空 | 显式指定端点/凭据 |
| `PI_AGENT_REASONING` | `true` | 是否启用推理 |
| `PI_AGENT_THINKING_LEVEL` | `medium` | 思考级别 |
| `PI_AGENT_EXPOSE_THINKING` | `false` | 是否对外暴露思考内容 |
| `PI_AGENT_EXPOSE_TOOL_RESULTS` | `false` | 是否对外暴露工具结果 |
| `PI_AGENT_CONTEXT_WINDOW` | `32768` | 注册上下文窗口。Ark Seed 2.1 的长 PPT 工具参数需更大窗口与输出余量,provider 必须支持所选值 |
| `PI_AGENT_MAX_OUTPUT_TOKENS` | `8192` | 单次输出 token 上限 |
| `PI_AGENT_MAX_TOOL_CALLS` | `12` | 单轮工具调用上限 |
| `PI_AGENT_MAX_RUN_SECONDS` | `1200` | 单次运行时限 |
| `PI_AGENT_TURN_RESERVE_SECONDS` | `60` | 轮次预留时间 |
| `PI_AGENT_MAX_LOADED_SESSIONS` | `50` | 已加载会话数上限 |
| `PI_AGENT_MAX_REQUEST_BYTES` | `1048576` | 请求体字节上限 |
| `PI_AGENT_TRANSCRIPT_DIR` | `/app/data/transcripts` | 会话转录目录(compose 固定) |

### MCP 工具超时(compose 供应)

| 变量 | 默认值 | 说明 |
|---|---|---|
| `TKB_MCP_URL` | `http://webapp:8000/mcp/` | Pi 访问引擎 MCP 端点的地址(compose 固定) |
| `TKB_CONTRACT_STRICT` | `true` | 严格契约模式 |
| `TKB_CONNECT_TIMEOUT_MS` | `10000` | 连接超时 |
| `TKB_TOOL_TIMEOUT_MS` | `60000` | 常规工具超时 |
| `TKB_DEEP_TOOL_TIMEOUT_MS` | `60000` | deep 检索工具超时 |
| `TKB_PPT_TOOL_TIMEOUT_MS` | `900000` | PPT 生成工具超时 |

### 会话记忆投递(BFF ↔ Pi;启用前先通过对应验收)

| 变量 | 默认值 | 说明 |
|---|---|---|
| `TKB_CONVERSATION_MEMORY_ENABLED` | `true` | 启用会话记忆投递 |
| `TKB_CONVERSATION_MEMORY_RELIABLE_DELIVERY` | `false` | 仅在 B2 验收通过后启用;启动 token 必须覆盖每条隔离绑定 |
| `PI_AGENT_MEMORY_DELIVERY_TOKENS` | `[]` | 每条隔离绑定的启动 token 配额 |
| `TKB_CONVERSATION_MEMORY_RECALL_TIMEOUT_MS` | `5000` | 记忆召回超时 |
| `TKB_CONVERSATION_MEMORY_RECALL_LIMIT` | `5` | 单轮召回条数 |
| `TKB_CONVERSATION_MEMORY_CONTEXT_BUDGET_CHARS` | `6000` | 注入上下文的字符预算 |
| `TKB_CONVERSATION_MEMORY_RETENTION_CONTEXT` | `Completed team conversation turn` | 留存上下文说明 |

### 信任范围绑定

| 变量 | 默认值 | 说明 |
|---|---|---|
| `MEMORY_SCOPE_BINDINGS` | `{}` | 可选的 SHA-256 凭据 → 受信范围映射,BFF 与 Pi 共享。B1 验收通过前保持为空/未设置。设计详见归档文档 `openspec/changes/archive/2026-09-15-align-hindsight-memory-capabilities/docs/memory-scope.md` |

### 工具授权(Agent 自建工具)

| 变量 | 默认值 | 说明 |
|---|---|---|
| `PI_AGENT_TOOL_AUTHORING_ENABLED` | `true` | 启用 Agent 编写工具(build tool-job 并启用 tool-authoring profile) |
| `PI_AGENT_RUNNER_URL` | 空 | tool-runner 网关 URL |
| `PI_AGENT_RUNNER_TOKEN` | 空 | 网关令牌——生成私有随机值,**切勿提交配置值** |
| `PI_AGENT_TOOL_LIBRARY_DIR` | 空(compose 固定 `/app/tool-library`) | 工具库目录 |
| `PI_AGENT_MAX_CODE_JOBS` | `12` | 代码任务数上限 |
| `PI_AGENT_MAX_BUILD_ATTEMPTS` | `3` | 构建尝试次数上限 |
| `PI_AGENT_TIMEZONE` | `Asia/Shanghai` | 运行时时区 |

---

## tool-runner

| 变量 | 默认值 | 说明 |
|---|---|---|
| `TOOL_RUNNER_DOCKER_SOCKET` | `/var/run/docker.sock` | 网关挂载的**宿主机** socket(容器内路径固定)。默认是 Docker daemon socket;rootless podman 部署改设用户的 compat socket。详见 `src/extensions/tool-runner/README.md` |
| `RUNNER_PUBLIC_HTTP` | `true` | tool-job 容器是否允许对外 HTTP |
| `RUNNER_JOB_IMAGE`(compose) | `team-kb-tool-job:latest` | 网关用来创建一次性容器的镜像 |
| `COMPOSE_PROFILES` | 空 | 要启动的 compose profile(`tool-authoring` 追加 tool-runner 网关)。留空/未设置 = 不启用工具授权 |

---

## 构建

### Debian 镜像(compose build args,注释掉 = 默认)

固定系统包(含 LibreOffice 与 Noto CJK)装在与源无关的缓存层。上游路由不稳定时可选用区域 Debian 镜像;普通源与安全源需成对配置。留空即用官方端点。

| 变量 | 默认值 | 说明 |
|---|---|---|
| `DEBIAN_MIRROR` | 空(官方) | 例:`https://mirrors.aliyun.com/debian` |
| `DEBIAN_SECURITY_MIRROR` | 空(官方) | 例:`https://mirrors.aliyun.com/debian-security` |

### npm 镜像

| 变量 | 默认值 | 说明 |
|---|---|---|
| `NPM_REGISTRY` | `https://registry.npmmirror.com` | 镜像构建中 `npm ci` 的取包源。默认区域镜像是因为部分局域网主机直连 registry.npmjs.org 会停滞;`npm ci` 会校验每个 tarball 与 package-lock 的完整性哈希,镜像可以扣包但无法替换内容。可指向 `https://registry.npmjs.org` 或其他镜像覆盖 |
| `NPM_AUDIT_REGISTRY` | `https://registry.npmjs.org` | `npm run security` 审计的源。**刻意默认官方源**:镜像源不实现 npm 的 audit API,安全门是 fail-closed——指向镜像会阻断构建,属设计行为 |
| `NPM_PROXY` | 空 | 构建期 npm 取包代理(npm 不读 HTTP(S)_PROXY,只读 npm_config_proxy)。仅本机按需启用,永不提交机器专属值 |

### 其他构建变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `PYPI_MIRROR`(compose/pipeline) | 空 | uv 依赖安装的 PyPI 镜像;由 CI/CD pipeline 注入,手工 `compose build` 用上游 |
| `GIT_COMMIT`(compose/pipeline) | 空 | 源码 SHA,`GET /version` 上报;由 pipeline 注入 |
| `COMPOSE_PROJECT_NAME`(compose) | `team-kb` | 所有带命名空间的名字(容器、镜像、卷)都由它派生。LAN 预发环境用 `team-kb-dev` |

---

## `config/app.yaml`

### `engine`(顶层)

| 键 | 默认值 | 说明 |
|---|---|---|
| `engine.impl` | `graphrag` | 引擎实现名 |
| `engine.config` | `config/engine/graphrag` | 引擎配置目录 |

### MCP 工具负载上限(`ENGINE_TOOLS_*` 环境变量提供,env 前缀 `ENGINE_TOOLS_`;app.yaml 中仅作文档——Rest API 不受限,只有 MCP 工具层读取)

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `ENGINE_TOOLS_WINDOW_CHARS` | `8000` | `tkb_get_document` 单次正文窗口(字符) |
| `ENGINE_TOOLS_LIST_PAGE_MAX` | `50` | `tkb_list_documents` page_size 封顶 |
| `ENGINE_TOOLS_DEEP_EXCERPT_CHARS` | `2000` | deep 检索单条证据摘录上限(字符) |
| `ENGINE_TOOLS_DEEP_TOTAL_CHARS` | `12000` | deep 检索证据总预算(字符) |
| `ENGINE_TOOLS_ENTITIES_MAX` | `10` | 检索响应 related_entities 条数上限 |
| `ENGINE_TOOLS_RESPONSE_MAX_CHARS` | `24000` | 检索工具整个序列化响应的字符预算;超出时先丢弃排名最低的 sources,并在 trace 中以 `payload_trimmed`/`payload_kept`/`payload_dropped` 报告裁剪 |

### `engine.ingest`

| 键 | 默认值 | 说明 |
|---|---|---|
| `vector_only` | `true` | 仅向量摄取 |
| `chunk_concurrency` | `4` | 单文档分块并行度 |
| `doc_concurrency` | `2` | 跨文档并行度 |
| `llm_retries` | `3` | 模型端点瞬时失败(超时/连接错误/HTTP 429/5xx)的有界重试次数;`0` 关闭重试 |
| `llm_backoff_base_seconds` | `2` | 指数退避 + 抖动的基数,单次退避上限 30s |

### `engine.memory`

| 键 | 默认值 | 说明 |
|---|---|---|
| `enabled` | `true` | 记忆总开关 |
| `fact_cache_capacity` | `256` | 事实缓存容量 |
| `fact_cache_ttl_seconds` | `1800` | 事实缓存 TTL |
| `fact_context_limit` | `8` | 注入上下文的事实条数上限 |
| `fact_context_max_tokens` | `1200` | 事实上下文 token 预算 |
| `retain_chunk_concurrency` | `4` | 留存分块并行度 |
| `entity_resolution_max_concurrent` | `8` | 实体消歧并发上限 |
| `entity_resolution_timeout_seconds` | `60` | 实体消歧超时 |
| `graph_worker` | `true` | Neo4j 投影 worker 主开关(见上文 `HINDSIGHT_GRAPH_WORKER_ENABLED`) |
| `consolidation_batch_size` | `32` | 记忆合并批大小 |
| `consolidation_max_iterations` | `100` | 合并最大迭代数 |
| `consolidation_max_tokens` | `3200000` | 合并 token 预算 |
| `consolidation_llm_timeout_seconds` | `300` | 合并 LLM 调用超时 |
| `consolidation_lease_seconds` | `720` | 合并任务租约 |

`engine.memory.features` 为能力开关,当前全部开启:`scope`、`reliable_retention`、
`entity_resolution`、`consolidation`、`evidence_retrieval`、`mental_models`、
`adaptive_reflect`。

### `plugin`

| 键 | 默认值 | 说明 |
|---|---|---|
| `plugin.impl` | `tkb` | 插件实现名 |

### `archive`(自动归档流水线)

归档流水线默认关闭。`enabled: false` 是提交进镜像的默认值,是否开启由**单个部署**用
环境变量决定,不需要改仓库里的配置,也不需要重建镜像。

| 键 | 默认值 | 说明 |
|---|---|---|
| `enabled` | `false` | 流水线总开关,`ARCHIVE_ENABLED` 覆盖。开启的部署才启动归档扫描与 worker |
| `threshold` | `0.75` | 自动执行置信度下界:达到即自动归档,否则转为待确认。`ARCHIVE_THRESHOLD` 覆盖 |
| `delta` | `0.10` | V1 兼容字段,V2 二态分流不再使用。`ARCHIVE_DELTA` 覆盖 |
| `review_all` | `false` | 全部转待确认,不自动执行。`ARCHIVE_REVIEW_ALL` 覆盖 |
| `poll_seconds` | `5` | 扫描轮询间隔(秒)。`ARCHIVE_POLL_SECONDS` 覆盖 |
| `stability_checks` | `2` | 判定文件已稳定的连续检查次数。`ARCHIVE_STABILITY_CHECKS` 覆盖 |
| `max_attempts` | `5` | 单文件最大尝试次数。`ARCHIVE_MAX_ATTEMPTS` 覆盖 |
| `top_k` | `5` | 归档目标检索条数。`ARCHIVE_TOP_K` 覆盖 |
| `collision_policy` | `suffix` | 目标已有同名文件时:`suffix` 确定性加后缀 / `block` 拒绝。`ARCHIVE_COLLISION_POLICY` 覆盖 |

`ARCHIVE_ENABLED` 是**三态**的:未设置时回落 app.yaml 的值,显式 `true`/`false` 才覆盖它。
因此开工即关的部署不受影响,而 app.yaml 打开时仍可用 `ARCHIVE_ENABLED=false` 单独关掉某个部署。

`ARCHIVE_WORKSPACE_DIR`(默认 `workspace`,compose 部署为 `/app/workspace`)是唯一
只来自环境、app.yaml 中不存在的项:inbox 与归档树都建在它下面。

Compose 部署的透传清单:`docker-compose.yml` 的 webapp 服务显式列出
`ARCHIVE_WORKSPACE_DIR`、`ARCHIVE_ENABLED`、`ARCHIVE_THRESHOLD`、`ARCHIVE_DELTA`、
`ARCHIVE_REVIEW_ALL`、`ARCHIVE_POLL_SECONDS`。只有列进该映射的变量才会进入容器进程环境
(pipeline 的 `deploy.env` 经 `--env-file` 提供值),其余几个旋钮在 compose 部署中需要改 app.yaml。

开启是每个部署自己的动作:LAN 上预发在 `.deploy/develop/deploy.env` 里加一行
`ARCHIVE_ENABLED=true` 即可,生产不写就保持关闭。回滚 = 改回 `false`(或删掉该行)后重启该 stack。
