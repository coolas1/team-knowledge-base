# engine/ -- GraphRAG knowledge storage and retrieval

## Module summary

The GraphRAG engine: ingests documents into a three-layer knowledge graph
(entities, relations, chunks) and retrieves via semantic search with reranking.
Exposes a CLI, an MCP server, and a programmatic interface. Backed by
Postgres+pgvector (vectors/chunks) and Neo4j (entity/relation graph).

- `cli.py` / `mcp.py` — CLI and MCP server entrypoints (`python -m src.engine.cli` / `.mcp`).
- `interface.py` — programmatic API surface for the engine.
- `config.py` — engine-specific settings.
- `components/` — pipeline stages:
  - `extractors/` — per-format document extractors (pdf, docx, pptx, markdown, image/OCR) + `registry.py`.
  - `chunker.py`, `analyzer.py`, `embedder.py`, `reranker.py` — chunk, analyze, embed, rerank.
  - `store/` — persistence (`models.py`, `postgres.py`).
- `graphrag/` — GraphRAG orchestration: `backend.py` (store impl), `pipeline.py` (ingest), `_search.py` (retrieval).

## Hard-won knowledge

<!-- Inclusion rule: add an entry only if it is non-obvious, repo-related, and
     painful to re-derive. Each entry: the decision (1-3 sentences) + why.
     Truly long-form decisions link out to a doc instead of bloating this file. -->
