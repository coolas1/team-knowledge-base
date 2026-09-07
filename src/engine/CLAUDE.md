# engine/ -- GraphRAG knowledge storage and retrieval

## Module summary

The GraphRAG engine: ingests documents into a three-layer knowledge graph
(entities, relations, chunks) and retrieves via semantic search with reranking.
Exposes a CLI and a programmatic interface. Backed by
Postgres+pgvector (vectors/chunks) and Neo4j (entity/relation graph).

- `cli.py` - CLI entrypoint (`python -m src.engine.cli`); honors `engine.memory`.
- `interface.py` - programmatic API surface for the engine.
- `config.py` - `EngineConfig` (+`MemorySettings`), `engine_config_from_app`,
  `build_engine` dispatch on `engine.impl`.
- `components/` - pipeline stages:
  - `extractors/` - per-format document extractors (pdf, docx, pptx, markdown, csv, image/OCR) + `registry.py`.
  - `chunker.py`, `analyzer.py`, `embedder.py`, `reranker.py` - chunk, analyze, embed, rerank.
  - `store/` - persistence (`models.py`, `postgres.py`).
- `graphrag/` - GraphRAG orchestration: `backend.py` (store impl; optional
  memory enrichment + retain-hook wiring in `build()`), `pipeline.py` (ingest;
  parallel chunk analysis + batched Neo4j writes, bounded by
  `engine.ingest.*` concurrency knobs), `_search.py` (retrieval).
- `memory/` - memory (Hindsight) capabilities, selected by the
  `engine.memory.*` flags in `config/app.yaml`:
  - retain pipeline (`retain.py`, `retain_hook.py`) - runs at index time.
  - reflective query (`query.py`, `service.py`, `recall.py`, `reflect.py`,
    `compat.py`) - the `KnowledgeQuery` contract impl.
  - graph projection (`graph_outbox.py`, `graph_projector.py`,
    `graph_runtime.py`, `neo4j_graph.py`) - outbox worker to Neo4j.
  - `enrich.py` - failure-isolated `MemoryStateEnricher` used by the backend.
  - `repository.py`, `models.py`, `types.py`, `providers.py`, `backfill.py`.

## Hard-won knowledge

<!-- Inclusion rule: add an entry only if it is non-obvious, repo-related, and
     painful to re-derive. Each entry: the decision (1-3 sentences) + why.
     Truly long-form decisions link out to a doc instead of bloating this file. -->
