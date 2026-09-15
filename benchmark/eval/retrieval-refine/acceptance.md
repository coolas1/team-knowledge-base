# Retrieval refinement acceptance record

Recorded: 2026-09-15 (Asia/Shanghai)

Implementation head before this record: `8d03e07adaf73b89c8a639cdec53a78e49feadf7`

## Accepted behavior

- Default knowledge recall reserves the full requested quota for current uploaded documents and independently adds at most two quality-gated conversation memories.
- Compatibility `sources` and `document_evidence` contain only document-authority records. Auxiliary memories appear only in `conversation_context` and cannot become document citations.
- Upload and conversation candidates are retrieved and ranked in separate pools; raw BM25 values never compete across the two corpora.
- Document lexical retrieval weights title, filename, overview, tags/entities, and body independently. Dense chunk embeddings use original chunk text.
- Hierarchical reads select revision-fenced document retrieval records before bounded original-passage selection, with a safe flat-read fallback while migration is incomplete.
- Deep search skips unnecessary expansion phases, keeps deterministic evidence on reranker degradation, and attempts at most one document-only fallback after all deep arms fail.
- Selective retention publishes only policy-eligible user-originated durable information and excludes ordinary assistant output.

## Verification evidence

- `uv run ruff check .`: passed.
- `uv run pytest -q src/engine/hindsight_components/tests`: 239 passed.
- Filtered repository suite: 512 passed, 40 skipped. Two stale memory-parity collectors were excluded because the working tree independently moves `benchmark/memory-parity` to `benchmark/eval/memory-parity`; those user-owned changes are not part of this change.
- `npm test` in `src/extensions/pi-agent`: 122 passed.
- `RUN_INTEGRATION=1 uv run pytest -q tests/integration/test_retrieval_view_backfill.py`: 1 passed against PostgreSQL/pgvector.
- Retrieval fixture validator: 12 cases, digest `0ba5bdf6e893efb4d444026f14b332192973223aa4ef3707c4e8937b744096f8`.
- Scale fixture: 30,000 records, indexed candidate bound 300, digest `75c9617f8bf4908699a357a6bc70c079139bd808f19d83df8a71c9f5bd59c142`.
- `docker compose config --quiet`: passed; Docker reported only that its user-level config file was unreadable.
- `openspec validate separate-conversation-memory-from-knowledge-evidence --strict`: passed.

## Rollout boundaries

The new mixed-source, hierarchical-read, adaptive-deep, and selective-retention paths remain independently switchable. Default auxiliary knowledge memory is enabled with a cap of two and can be disabled immediately with `HINDSIGHT_KNOWLEDGE_MEMORY_CONTEXT_ENABLED=false` without disabling document search.

Production cleanup, production-scale latency collection, and staged rollout remain operational actions and are intentionally not recorded as completed OpenSpec tasks by this code-only acceptance run.
