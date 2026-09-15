# Team Knowledge Base

面向团队的 GraphRAG 知识库:文档入库后构建**三层知识图谱**(实体 → 关系 →
文本块),经 pgvector 语义检索与可选 rerank,通过 Web UI、MCP 服务或 CLI
查询;可选记忆能力(反思式检索、记忆图谱)。

## 功能

- **GraphRAG 检索** — 基于 Postgres + pgvector 的向量检索,叠加 Neo4j
  实体/关系图谱。
- **多格式入库** — Web 上传支持 Markdown / TXT / PDF / DOCX / PPTX 及
  常见图片格式(OCR)。
- **可插拔 reranker** — 外部 `/v1/rerank` API、本地 CrossEncoder(需
  `uv sync --extra reranker`)或关闭。
- **记忆能力** — `config/app.yaml` 的 `engine.memory.*` 开关:留存管线、
  反思式查询、Neo4j 记忆图谱 worker。
- **四个入口** — Web UI(SPA + BFF)、MCP 服务(挂载于 `/mcp`)、CLI
  (`src.engine.cli`)、对话 Agent(Pi Agent sidecar)。
- **Agent 文档生成** — 对话中可直接生成可下载的 Word / PDF / PowerPoint
  文件(PPT 同时附带可编辑的 Slidev Markdown 源码)。

## 快速开始(本地开发)

前置:Python ≥ 3.12 与 [`uv`](https://docs.astral.sh/uv/)、Node.js、
Docker 或 Podman。

```bash
git clone https://github.com/coolas1/team-knowledge-base.git
cd team-knowledge-base
uv sync                        # 本地 torch reranker 才需要 --extra reranker
cp .env.example .env           # 编辑 .env,至少设置 EMBEDDING_BASE_URL 与 LLM_BASE_URL
docker compose up -d           # 后备服务:Postgres+pgvector(:5433)、Neo4j(:7687/:7474)
```

启动应用:

```bash
uv run uvicorn src.frontend.webapp.server.app:app --reload   # BFF + 引擎,:8000,含 /mcp
cd src/frontend/webapp/client && npm install && npm run dev   # SPA,:5173,代理 /api → :8000
uv run python -m src.engine.cli recall --query "示例查询"     # CLI
```

配置项说明见 [`docs/config-reference.md`](docs/config-reference.md);
容器化完整启动步骤见 [`docs/start.md`](docs/start.md)。

## 访问部署(局域网)

LAN 部署由 `cicd/` 的两条独立流水线管理:**生产**跟随 `main`(:8000),
**预发**跟随 `develop`(:8001),每 5 分钟轮询、lint + 测试通过后自动
SHA 标签镜像并重新部署。请以客户端身份访问已发布端口,不要手工
`docker/podman compose up` 操作流水线的 compose 项目;流水线细节与回滚
手册见 [`cicd/README.md`](cicd/README.md)。

## 使用手册

- [快速启动(容器化)](docs/start.md) — 完整启动步骤、模型准备、常用入口
- [配置参考](docs/config-reference.md) — `.env` 与 `config/app.yaml` 全部配置项
- [架构概览](docs/architecture.md)(English)
- [图片/PPT 生成运维](docs/ark-image-ppt.md)
- [深度检索运维](docs/deep-search-operations.md)(English)

## 参与贡献

从 `develop` 切出特性分支,PR 合回 `develop`(PR 描述请使用
[模板](.github/PULL_REQUEST_TEMPLATE.md));推送前跑
`uv run ruff check && uv run pytest`。`develop` 由维护者定期合入 `main`
并发版(版本号在 `VERSION` 与 `pyproject.toml`)。完整开发约定、命令与
架构说明见 [`CLAUDE.md`](CLAUDE.md)。
