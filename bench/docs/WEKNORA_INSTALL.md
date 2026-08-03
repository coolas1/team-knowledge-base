# WeKnora install guide (Linux host + sibling container)

RAG Agent Bench evaluates [WeKnora](https://github.com/Tencent/WeKnora) against
the `raw/` corpus alongside gbrain and team-knowledge-base. WeKnora is an LLM-powered RAG
framework — a Docker/Podman Compose stack (Go app + ParadeDB pgvector postgres
+ docreader + redis). Unlike gbrain it parses PDF/Word/Excel/CSV/images
natively, so the full-corpus run ingests the binary "leaf node" attachments
gbrain leaves un-parsed.

The bench harness lives in this repo (`scripts/weknora/`); the WeKnora **source**
is vendored here as a git submodule at `vendors/WeKnora` (pinned; init with
`git submodule update --init --recursive vendors/WeKnora`). Everything the bench
writes goes to `weknora-files/` (gitignored).

## Two execution contexts

- **Host** — runs the stack under `podman` (or `docker`). WeKnora publishes
  `:8080`. Run `install-stack.sh` and the full `run-all.sh` here.
- **Bench container** (this Claude Code box) — has no podman/docker/systemd, so
  it cannot run the stack. It drives the running server over HTTP instead. Point
  it at the host's published port via the bridge gateway, e.g.
  `WEKNORA_BASE_URL=http://172.17.0.1:8080` (detect with `ip route` → default
  gateway, typically `172.17.0.1` on a `docker0` bridge).

The single connectivity knob is **`WEKNORA_BASE_URL`** (env, or
`weknora-files/env/url`, default `http://localhost:8080`). Both the curl-based
pipeline and the `weknora` CLI profile use it.

## Prerequisites

| Tool | Why | Install |
|---|---|---|
| **podman** + `podman compose` (or Docker + `docker compose`) | Run the WeKnora stack | system package manager |
| **git** | Init the `vendors/WeKnora` submodule | system package manager |
| **jq**, **curl** | The ingest/health/bootstrap scripts use the REST API | system package manager |
| **Ollama** (or another embedding + chat provider) | Embeddings + LLM | see below |
| **Go ≥ 1.26** *(optional)* | Build the `weknora` CLI (for `mcp serve` / interactive use) | https://go.dev/dl/ |

`install-stack.sh` auto-detects `podman compose` → `docker compose` →
`docker-compose`. `install-cli.sh` builds the CLI from source (no prebuilt
binaries ship on WeKnora's releases), installing Go 1.26 if absent.

## Embedding + LLM provider

The bench uses a **remote Ollama** at `10.201.186.15:11434` serving
`nomic-embed-text` (embeddings, 768-dim) **and** `qwen3.5:9b` (chat LLM). One
provider covers WeKnora's embedding + summary + VLM needs. Verify:

```bash
curl http://10.201.186.15:11434/api/tags   # lists nomic-embed-text + qwen3.5
```

`install-stack.sh` bakes `OLLAMA_BASE_URL=http://10.201.186.15:11434` into the
generated `.env`. To swap providers (OpenAI/DeepSeek/Qwen-cloud/…), edit
`weknora-files/env/.env` and configure models in the WeKnora Web UI
(`http://localhost:8080`).

## Install + launch (host)

```bash
# 1. Init the WeKnora submodule (one-time, after cloning this repo).
git submodule update --init --recursive vendors/WeKnora

# 2. Bring up the stack (generates .env, podman-compose up -d, waits healthy).
bash scripts/weknora/install-stack.sh

# 3. (Optional) build the weknora CLI for interactive commands + MCP.
bash scripts/weknora/install-cli.sh
```

`install-stack.sh` is idempotent — re-running re-pulls images and brings up
missing services **without rotating secrets** (so existing data survives). Bump
the pinned WeKnora version with `cd vendors/WeKnora && git checkout <tag> &&
cd ../.. && git add vendors/WeKnora`. On first boot WeKnora seeds default Ollama
models; `bootstrap.sh` resolves and reuses them.

Web UI: `http://localhost` · API: `http://localhost:8080/api/v1` · Swagger:
`http://localhost:8080/swagger`.

## Run the bench pipeline

```bash
# All-in-one: bootstrap (tenant + KB) -> reset -> ingest raw/ -> health.
bash scripts/weknora/run-all.sh

# Or granular:
bash scripts/weknora/bootstrap.sh      # POST /tenants -> api_key; create KB wren-adachi-corpus
bash scripts/weknora/ingest.sh         # upload raw/ + poll parse -> manifest-<ts>.json
bash scripts/weknora/health.sh         # doc counts by parse_status + raw/ parity
bash scripts/weknora/qa-predict.sh     # WeKnora answers to the 30 eval questions

# gbrain-parity run (markdown only, 14 files) for an apples-to-apples comparison:
bash scripts/weknora/ingest.sh --only-md
```

From the **bench container**, prefix the host URL:

```bash
WEKNORA_BASE_URL=http://172.17.0.1:8080 bash scripts/weknora/health.sh
WEKNORA_BASE_URL=http://172.17.0.1:8080 bash scripts/weknora/connect.sh   # configure CLI/MCP here
```

## Expected outcome (full corpus)

- 29 documents ingested from `raw/` (14 `.md` + pdf/xlsx/csv/png/jpg).
- All `parse_status: completed`; `weknora-files/manifest-<ts>.json` lists each
  file with its `doc_id`.
- Spot check: `weknora chat "What logger model did Wren use for SST?"` returns
  **HOBO U22-001** — grounded in `coral-resilience-paper.pdf`, which gbrain
  (`.md`-only) cannot retrieve.

## Gotchas

- **Headless bootstrap.** `bootstrap.sh` mints the tenant via
  `POST /api/v1/tenants`, whose response carries `api_key` with no auth header.
  If your WeKnora version gates that call, register a user in the Web UI, grab
  the API key from the account page, export `WEKNORA_API_KEY=sk-...`, and re-run
  `bootstrap.sh` — it will reuse it.
- **Model IDs are WeKnora-registry UUIDs**, not Ollama model names. `bootstrap.sh`
  resolves them from the running instance (and registers the two Ollama models if
  none are seeded). If KB create fails on model ids, configure embedding + chat
  models in the Web UI once and re-run.
- **podman / SELinux.** On enforcing SELinux hosts the bind-mounted
  `./config/config.yaml` needs a `:Z` relabel. If compose fails with a
  permission-denied on the config mount, add `:Z` in a local
  `docker-compose.override.yml` (or `setenforce 0` for a dev box). Rootless
  podman's published `:8080` is reachable from sibling containers at the host
  gateway.
- **`host.docker.internal`.** Under podman this is `host.containers.internal`,
  but it's irrelevant here — WeKnora reaches Ollama via the routable remote IP.
- **Ephemeral containers.** The WeKnora source survives in the submodule
  (committed), but the Docker images + postgres/redis volumes are wiped when the
  host resets. Re-run `install-stack.sh` (re-pulls images; reuses `.env` if
  present) and `install-cli.sh` (if you use the CLI).
- **MCP.** `.mcp.json` registers `weknora mcp serve` (mirrors gbrain's
  `gbrain serve`). It needs the CLI built (`install-cli.sh`) and the profile
  configured (`bootstrap.sh` on the host, or `connect.sh` from the bench
  container). Until then `/mcp` shows a reconnect failure for weknora — same
  invariant as gbrain-before-install.

## What this bench deliberately does NOT do

- **No source-mutation.** `raw/` is read-only; only the multipart `file=@<path>`
  upload (a read) touches it.
- **No source edits.** The `vendors/WeKnora` submodule is used read-only (compose
  + bind-mounts); no patches are maintained against it.
- **No frontmatter.** The corpus markdown is pure prose; WeKnora ingests files
  as documents (no graph extraction from inline links — that's gbrain's niche).
- **No enforced parity.** By default WeKnora ingests the full multimodal corpus
  (its strength). Use `--only-md` for a controlled comparison with gbrain.
