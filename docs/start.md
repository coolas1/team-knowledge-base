# TKB 快速启动

## 1. 首次启动

从工作区根目录进入项目：

```powershell
cd .\projects\team-knowledge-base
Copy-Item .env.example .env
```

按需修改 `.env`，默认启动完整服务（包含 Pi Agent）：

```powershell
docker compose -f .\docker-compose.yml up -d --build
```

服务入口：

- Web：`http://localhost:8000`
- API 文档：`http://localhost:8000/docs`
- MCP：`http://localhost:8000/mcp/`
- Neo4j Browser：`http://localhost:7474`
- Pi Agent：`http://127.0.0.1:8010`

如临时不需要 Pi Agent，可在启动后停止它：

```powershell
docker compose -f .\docker-compose.yml stop pi-agent
```

Pi Agent 只对本机开放，默认继承 `.env` 中的 `LLM_*` 模型配置。

## 2. 使用本地 Ollama 模型

在 `.env` 中配置：

```dotenv
LLM_PROVIDER=ollama
LLM_MODEL=qwen3:14b
LLM_BASE_URL=http://ollama:11434/v1
LLM_API_KEY=ollama
```

首次使用时安装模型：

```powershell
docker exec team-kb-ollama ollama pull qwen3:14b
docker exec team-kb-ollama ollama pull nomic-embed-text
```

其中 `qwen3:14b` 用于大模型生成，`nomic-embed-text` 用于本地 Embedding。

## 3. 使用外部大模型 API

标准 OpenAI-compatible API 在 `.env` 中配置：

```dotenv
LLM_PROVIDER=custom
LLM_MODEL=供应商提供的模型名称
LLM_BASE_URL=https://供应商地址/v1
LLM_API_KEY=真实API密钥
```

Pi Agent 默认继承上述 `LLM_*` 配置，无需重复填写 `PI_AGENT_PROVIDER`、
`PI_AGENT_MODEL`、`PI_AGENT_BASE_URL` 和 `PI_AGENT_API_KEY`。外部 API 负责
Chat、分析和 Hindsight reflect；Embedding 仍使用本地 `nomic-embed-text`。

注意：

- `LLM_BASE_URL` 填 API 根地址，不要包含 `/chat/completions`。
- API 需要兼容 OpenAI `/chat/completions` 和 JSON Object 响应模式。
- 不要把真实 API Key 写入 `.env.example` 或提交到 Git。
- 使用外部 API 执行 deep recall、reflect 或 retain 时，相关知识片段可能发送给外部供应商。
- Pi Agent 默认不会向浏览器输出模型思考过程和 MCP 原始结果。

## 4. 让配置生效

只修改 `.env` 时无需重新构建镜像：

```powershell
docker compose -f .\docker-compose.yml up -d --force-recreate webapp pi-agent
```

修改代码后使用：

```powershell
docker compose -f .\docker-compose.yml up -d --build webapp pi-agent
```

## 5. API 与 MCP

REST 查询接口：

```text
POST http://localhost:8000/api/query
```

可以直接在 `http://localhost:8000/docs` 中测试。

Cherry Studio 等客户端使用 MCP 地址：

```text
http://localhost:8000/mcp/
```

常用 MCP 工具：

- `list_documents`：查看知识库文件。
- `search_knowledge_fast`：简单事实、定义和明确关键词。
- `search_knowledge_deep`：跨文档比较、多跳关系和综合分析。
- `query_knowledge`：手动指定 recall/reflect 和 fast/deep。

## 6. 停止服务

```powershell
docker compose -f .\docker-compose.yml down
```

不要添加 `-v`，否则会删除数据库和模型数据卷。
