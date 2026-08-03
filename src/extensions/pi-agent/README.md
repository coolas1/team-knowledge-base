# TKB Pi Agent Runtime

Headless Pi Agent runtime for Team Knowledge Base. It connects to the existing
Engine MCP endpoint, exposes a curated tool set to the model, persists sessions,
and provides an HTTP/SSE API for later BFF or UI integration.

## Safe defaults

The model receives these read-only TKB tools by default:

- `tkb_query_knowledge`
- `tkb_search_fast`
- `tkb_search_deep`
- `tkb_get_document`
- `tkb_query_graph`
- `tkb_list_documents`

Legacy search, document writes, and full-graph output remain opt-in. Pi's
built-in shell and file-editing tools are not enabled. The only local read tool
is restricted to Markdown files under this package's `skills` directory.

The bundled skills guide the model to choose Fast or Deep retrieval, perform
Reflect before Recall for reflective research, and cite retrieved sources.

## Start

Requires Node.js 22.19 or newer and a running TKB Engine MCP service.

```bash
npm install
npm run check
npm start
```

The default address is `http://127.0.0.1:8010`. `npm run smoke` performs a real
model-and-MCP query after the package has been built.

## Model configuration

Local Ollama is the default:

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
| `TKB_CONTRACT_STRICT` | `true` | Fail startup on MCP contract drift |
| `TKB_ENABLE_LEGACY_SEARCH` | `false` | Enable legacy `tkb_search` |
| `TKB_ENABLE_WRITE_TOOLS` | `false` | Enable upload and remove tools |
| `TKB_ENABLE_FULL_GRAPH` | `false` | Enable full-graph output |

When deployed beside the current Compose services, use
`TKB_MCP_URL=http://webapp:8000/mcp/`.

## HTTP/SSE API

- `GET /health`
- `POST /v1/sessions`
- `GET /v1/sessions`
- `GET /v1/sessions/:id`
- `DELETE /v1/sessions/:id`
- `POST /v1/sessions/:id/cancel`
- `POST /v1/sessions/:id/messages` with `{ "message": "..." }`

The message endpoint streams typed SSE events for assistant deltas, tool calls,
citations, limits, completion, and failures. Disconnecting the client cancels
the active run.

At startup the runtime validates all ten current Engine MCP tools. Only the
curated subset is exposed to the model unless an opt-in flag is set.
