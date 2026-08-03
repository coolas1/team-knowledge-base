# TKB Pi Agent Adapter

Pi extension that maps the Team Knowledge Base MCP contract to curated Pi
tools. It is the transport adapter only; the product Agent runtime and HTTP
service are added in the next deployment batch.

## Safe defaults

The extension registers these read-only tools by default:

- `tkb_query_knowledge`
- `tkb_search_fast`
- `tkb_search_deep`
- `tkb_get_document`
- `tkb_query_graph`
- `tkb_list_documents`

Legacy search, document writes, and full-graph output are implemented but
disabled unless explicitly enabled.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `TKB_MCP_URL` | `http://localhost:8000/mcp/` | Engine MCP endpoint |
| `TKB_CONNECT_TIMEOUT_MS` | `10000` | MCP connection timeout |
| `TKB_TOOL_TIMEOUT_MS` | `60000` | Normal tool timeout |
| `TKB_DEEP_TOOL_TIMEOUT_MS` | `300000` | Deep/reflect timeout |
| `TKB_CONTRACT_STRICT` | `true` | Fail session startup on contract drift |
| `TKB_ENABLE_LEGACY_SEARCH` | `false` | Register `tkb_search` |
| `TKB_ENABLE_WRITE_TOOLS` | `false` | Register upload and remove tools |
| `TKB_ENABLE_FULL_GRAPH` | `false` | Register full-graph output |

Inside Docker Compose, set `TKB_MCP_URL=http://webapp:8000/mcp/`.

## Development

Requires Node.js 22.19 or newer.

```bash
npm install --ignore-scripts
npm run check
```

For a standalone Pi smoke test while the TKB server is running:

```bash
pi -e ./src/index.ts
```

The extension validates all ten current Engine MCP tools at session startup.
Only the curated subset is exposed to the model by default.
