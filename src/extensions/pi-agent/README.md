# TKB Pi Agent Runtime

Headless Pi Agent runtime for Team Knowledge Base. It connects to the existing
Engine MCP endpoint, exposes a curated tool set to the model, persists sessions,
and provides an HTTP/SSE API for later BFF or UI integration.

## Safe defaults

The model receives these TKB tools by default:

- `tkb_query_knowledge`
- `tkb_search_fast`
- `tkb_search_deep`
- `tkb_get_document`
- `tkb_query_graph`
- `tkb_list_documents`
- `tkb_generate_document` (Word, PDF, PPTX + Slidev source)

Legacy search, document writes, and full-graph output remain opt-in. Pi's
built-in shell and file-editing tools are not enabled. The only local read tool
is restricted to Markdown files under this package's `skills` directory.

The bundled skills guide the model to choose Fast or Deep retrieval, perform
Reflect before Recall for reflective research, cite retrieved sources, and
generate downloadable office documents.

## Start

Requires Node.js 24 or newer and a running TKB Engine MCP service.

## Agent-authored tools

The runtime can fill missing capabilities by writing and executing JavaScript,
testing a parameterized implementation, and saving it to a shared versioned
tool library. `execute_code`, `find_tools`, `publish_tool` and `call_tool` are
the only new bootstrap tools; no date, calculator, conversion or web business
implementation is registered in advance. Generated code executes exclusively
in disposable job containers. Failed tools enter the SDK's error history so
the Agent can repair them within the run budget.

See [runner deployment and rollback](../tool-runner/README.md) for image builds,
authentication, network profiles, isolation limits and the independent library
volume. Without a configured runner, the existing TKB tools remain available
and execution reports an unavailable backend. Search requires an accessible
public endpoint or an administrator-configured search capability.

After building both TypeScript packages and the job image, run
`node scripts/authoring-smoke.mjs` from this package for live model acceptance.
It uses the repository's private `.env`, an empty temporary tool library and
the existing local MCP service. It creates no production sessions or shared
tools. A JSON evidence file records generated source, test/version metadata,
actual outputs and independent expected values; its path is printed at exit.
Never publish that file if prompts or outputs have been changed to private data.

```bash
npm ci
npm run check
npm start
```

The default address is `http://127.0.0.1:8010`. `npm run smoke` performs a real
model-and-MCP query after the package has been built.

`npm ci` is required while Pi `0.83.0` carries a vulnerable transitive lock.
The install hook removes only the verified vulnerable nested copy and makes Pi
resolve the pinned safe fallback. `npm run security` checks the lockfile, actual
installed files, runtime module resolution, and the npm production audit. The
container build runs this gate in both build and runtime stages.

## Compose deployment

The root `docker-compose.yml` contains the optional `pi-agent` profile. A normal
Compose start does not run the Agent. Enable it explicitly with the same file:

```bash
docker compose --profile pi-agent up -d --build pi-agent
```

Stop only the optional Agent with:

```bash
docker compose --profile pi-agent stop pi-agent
```

Its API is published on `127.0.0.1:8010` by default and sessions are stored in
the named `piagentdata` volume. Other Compose services continue to reach it by
the internal service name `pi-agent:8010`.

## Model configuration

Explicit `PI_AGENT_*` model settings take priority. If they are omitted, the
runtime inherits the existing `LLM_PROVIDER`, `LLM_MODEL`, `LLM_BASE_URL`, and
`LLM_API_KEY` settings. A disabled shared provider (`todo`, `none`, or
`disabled`) falls back to local Ollama:

| Variable | Default |
| --- | --- |
| `PI_AGENT_PROVIDER` | `ollama` |
| `PI_AGENT_MODEL` | `qwen3:14b` |
| `PI_AGENT_BASE_URL` | `http://localhost:11434/v1` |
| `PI_AGENT_API_KEY` | `ollama` |

For an external OpenAI-compatible service, set the provider name, model ID,
base URL, and API key through the same variables. `PI_AGENT_API` can override
the Pi model API adapter when required by a compatible provider.

## Runtime configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `TKB_MCP_URL` | `http://localhost:8000/mcp/` | Engine MCP endpoint |
| `PI_AGENT_HOST` | `127.0.0.1` | HTTP bind host |
| `PI_AGENT_PORT` | `8010` | HTTP port |
| `PI_AGENT_DATA_DIR` | `<cwd>/.pi-agent-data` | Runtime data root |
| `PI_AGENT_SESSION_DIR` | `<data>/sessions` | Persistent session files |
| `PI_AGENT_MAX_TOOL_CALLS` | `12` | Hard tool-call limit per run |
| `PI_AGENT_MAX_RUN_SECONDS` | `300` | Hard execution-time limit |
| `PI_AGENT_MAX_REQUEST_BYTES` | `1048576` | Maximum JSON request size |
| `PI_AGENT_EXPOSE_THINKING` | `false` | Include model reasoning deltas in SSE (local debugging only) |
| `PI_AGENT_EXPOSE_TOOL_RESULTS` | `false` | Include raw tool payloads in SSE (local debugging only) |
| `TKB_CONTRACT_STRICT` | `true` | Fail startup on MCP contract drift |
| `TKB_ENABLE_LEGACY_SEARCH` | `false` | Enable legacy `tkb_search` |
| `TKB_ENABLE_WRITE_TOOLS` | `false` | Enable upload and remove tools |
| `TKB_ENABLE_FULL_GRAPH` | `false` | Enable full-graph output |
| `TKB_CONVERSATION_MEMORY_ENABLED` | `false` | Enable automatic shared-team conversation recall and retention |
| `TKB_CONVERSATION_MEMORY_RECALL_TIMEOUT_MS` | `5000` | Timeout for pre-response conversation recall |
| `TKB_CONVERSATION_MEMORY_RECALL_LIMIT` | `5` | Maximum recalled conversation memories per turn |
| `TKB_CONVERSATION_MEMORY_CONTEXT_BUDGET_CHARS` | `6000` | Maximum injected historical-context size |
| `TKB_CONVERSATION_MEMORY_RETENTION_CONTEXT` | `Completed team conversation turn` | Label used by the engine retention worker |

When deployed beside the current Compose services, use
`TKB_MCP_URL=http://webapp:8000/mcp/`.

## HTTP/SSE API

- `GET /health`
- `POST /v1/sessions`
- `GET /v1/sessions`
- `GET /v1/sessions/:id` returns the session summary plus filtered
  `user`/`assistant` text messages
- `DELETE /v1/sessions/:id`
- `DELETE /v1/sessions/:id/memory` explicitly forgets retained memory for that
  session without deleting its JSONL history
- `POST /v1/sessions/:id/cancel`
- `POST /v1/sessions/:id/messages` with `{ "message": "..." }`

The message endpoint streams typed SSE events for assistant deltas, tool
status, citations, limits, completion, and failures. Model reasoning and raw
tool payloads are suppressed by default; citations are still derived on the
server. Disconnecting the client cancels the active run.

When conversation memory is enabled, completed visible user/assistant turns are
queued for asynchronous retention. Recall is injected only into the current
system prompt as bounded, untrusted historical evidence and is never persisted
as a visible message. The feature uses one shared team scope, so retained
conversation facts can be recalled by other sessions. Normal session deletion
does not forget memory; call the explicit memory DELETE endpoint when that is
intended. Queue counts and failures are exposed through `/health` without
returning retained content. Memory failures fail open so normal chat remains
available.

The Webapp image also runs a production dependency security gate. Its single
tracked exception is React Router's RSC-only `GHSA-qwww-vcr4-c8h2`: this Vite
SPA does not use RSC, SSR, or Server Actions, and no unaffected React Router
release is currently published. Any other high or critical advisory blocks the
image build.

At startup the runtime validates all eleven current Engine MCP tools. Only the
curated subset is exposed to the model unless an opt-in flag is set.
