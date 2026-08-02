# agent/ -- stateless skills and LLM orchestration

## Module summary

The stateless agent layer: skills that orchestrate the engine via an async
client, plus LLM and memory helpers. Skills are the reusable units an agent
host invokes.

- `engine_client.py` — async client wrapping the engine interface.
- `interface.py` — agent-facing API surface.
- `llm.py` — LLM provider abstraction.
- `memory.py` — conversation/memory helpers.
- `skills/` — discrete skills (`ingest_and_summarize.py`, `search_and_answer.py`).
- `codex/` — Codex-plugin adapter (`plugin.py`).

## Hard-won knowledge

<!-- Inclusion rule: add an entry only if it is non-obvious, repo-related, and
     painful to re-derive. Each entry: the decision (1-3 sentences) + why.
     Truly long-form decisions link out to a doc instead of bloating this file. -->
