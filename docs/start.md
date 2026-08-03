# TKB 快速启动

当前 `.env` 与 `docker-compose.yml` 已配置完整，可以直接启动。当前配置使用
外部 LLM API，Embedding 使用 Compose 内的 Ollama，Pi Agent 默认随服务启动。

## 1. 启动服务

需要 Docker Compose v2 和可用的 NVIDIA GPU 运行环境。进入项目目录：

```powershell
cd .\projects\team-knowledge-base
```

当前仓库已经有 `.env`，不要用模板覆盖。仅在新环境没有 `.env` 时执行：

```powershell
Copy-Item .env.example .env
```

新环境还需要编辑 `.env`，填写真实的数据库密码和 LLM 配置。启动完整服务：

```powershell
docker compose up -d --build
```

查看服务状态：

```powershell
docker compose ps
```

PostgreSQL、Neo4j 和 Pi Agent 变为 `healthy` 后，打开：

```text
http://localhost:8000
```

## 2. 首次准备 Ollama 模型

文档入库依赖本地 Embedding 模型。新机器首次使用时执行：

```powershell
docker compose exec ollama ollama pull nomic-embed-text
```

当前机器已经安装该模型，无需重复下载。

## 3. 模型配置

### 使用外部 API

当前 `.env` 使用 OpenAI-compatible API：

```dotenv
LLM_PROVIDER=custom
LLM_MODEL=供应商提供的模型名称
LLM_BASE_URL=https://供应商地址/v1
LLM_API_KEY=真实API密钥
```

`LLM_BASE_URL` 填 API 根地址，不要包含 `/chat/completions`。Pi Agent 默认
继承 `LLM_*`，不需要重复配置 `PI_AGENT_*`。

使用外部 API 进行文档分析、deep recall 或 reflect 时，相关知识片段可能
发送给外部供应商。不要提交包含真实 API Key 的 `.env`。

### 使用本地 Ollama

先安装生成模型：

```powershell
docker compose exec ollama ollama pull qwen3:14b
```

然后修改 `.env`：

```dotenv
LLM_PROVIDER=ollama
LLM_MODEL=qwen3:14b
LLM_BASE_URL=http://ollama:11434/v1
LLM_API_KEY=ollama
```

## 4. 常用入口

- Web UI：`http://localhost:8000`
- 知识库问答：`http://localhost:8000/ask`
- API 文档：`http://localhost:8000/docs`
- MCP：`http://localhost:8000/mcp/`
- Neo4j Browser：`http://localhost:7474`

Compose 使用 `8000` 作为正式入口。`5173` 仅用于 Vite 前端开发，并将
`/api` 请求代理到 `8000`。

## 5. 让修改生效

只修改 `.env`：

```powershell
docker compose up -d --force-recreate webapp pi-agent
```

修改 WebApp 或 Pi Agent 代码：

```powershell
docker compose up -d --build webapp pi-agent
```

## 6. 停止服务

```powershell
docker compose down
```

不要添加 `-v`，否则会删除数据库、Ollama 模型和 Pi Agent 历史会话。

如启动失败，可查看主要服务日志：

```powershell
docker compose logs --tail=100 webapp pi-agent
```
