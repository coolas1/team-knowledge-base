## 1. Configuration

- [x] 1.1 Add `engine.tools` settings (`window_chars`, `list_page_max`, `deep_excerpt_chars`, `deep_total_chars`) to `config/settings.py` with fallback defaults (8000 / 50 / 2000 / 12000) and document them in `config/app.yaml`. Verify with `uv run pytest tests/ -k settings` (or nearest existing settings test).

## 2. get_document windowing

- [x] 2.1 Add a windowed-text accessor to `GraphRAGBackend` (offset/limit over `Document.raw_text`, returning `text_window`/`total_chars`) without changing the existing `get_document` return shape used by REST. Verify with a unit test against a long fixture document.
- [x] 2.2 Change the MCP `get_document` tool in `src/agent/tkb/mcp/server.py`: add `offset`/`limit` params, return metadata + `text_window` + `offset` + `total_chars` + `has_more`, default limit = `engine.tools.window_chars`. Unit test: short doc → full text + `has_more: false`; long doc → bounded window + `has_more: true`; follow-up offset returns the next window.
- [x] 2.3 Update the sidecar's tool description for `tkb_get_document` (and any skill docs referencing it) to teach windowed reading. Verify by grepping the sidecar's tool metadata for the window parameters.

## 3. list_documents clamp

- [x] 3.1 Clamp `page_size` in the MCP `list_documents` wrapper to `engine.tools.list_page_max`, reporting the effective value in the response. Unit test: request 100 → response reports 50 and returns at most 50 items; REST `GET /api/documents?page_size=100` still returns 100 (BFF contract test).

## 4. Deep-search evidence budget

- [x] 4.1 Trim deep-search evidence in the engine's result shaping: per-excerpt cap `deep_excerpt_chars`, total budget `deep_total_chars`, drop lowest-ranked first, add `evidence_trimmed` + counts to the trace beside `degraded`. Unit test: oversized evidence set → total payload ≤ budget, trace reports trimming, top-ranked excerpts intact.
- [x] 4.2 Keep `pi.deep_search` sidecar telemetry truthful: a trimmed-but-successful search still logs `outcome: success` with the trim visible in the result (no code change expected — verify by reading the logging path and noting it in the PR).

## 5. Verification

- [x] 5.1 Full suite: `uv run ruff check` and `uv run pytest` pass.
- [x] 5.2 `RUN_INTEGRATION=1 uv run pytest -k "mcp or tools"` against live services; confirm tool response shapes.
- [ ] 5.3 Post-release (LAN): replay the incident question (lateral-control interpretation) and confirm the turn completes without `agent_failed`/`time_limit`; record evidence under `bench/`.
