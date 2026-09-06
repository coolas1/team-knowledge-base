## Context

Before this change, `config/settings.py` carried provider-era flat fields (`OLLAMA_BASE_URL`, `LLM_PROVIDER`, flat reranker variables) and each consumer implemented its own dispatch: the analyzer had `_call_ollama` plus an OpenAI path behind `LLM_PROVIDER`, hindsight providers had a native Ollama chat path, the embedder called Ollama's native `/api/embed`, and `build_llm()` used `LLM_PROVIDER=todo` as its off switch with a silent `gpt-4o-mini` model fallback. Ollama also serves OpenAI-compatible `/v1`, so the provider axis duplicated protocols without adding capability. See proposal.md - Why for the motivation.

## Goals / Non-Goals

**Goals:**
- One config axis per model role; self-documenting env names (`EMBEDDING_MODEL`, not a shared `MODEL`).
- Every consumer OpenAI-compatible only; deletion, not gating, of the native paths.
- Fail fast on misconfiguration; no silent fallbacks.

**Non-Goals:**
- No change to vector width (768, `EMBEDDING_DIM` in `store/models.py`); widening is a store migration, out of scope.
- No new reranker providers; `none | http | local` semantics are unchanged, only the config source moves.
- No secrets management beyond env vars.

## Decisions

### pydantic-settings sub-models with per-group env prefixes
`LLMSettings` / `EmbeddingSettings` / `RerankerSettings` are `BaseSettings` with `env_prefix="LLM_"/"EMBEDDING_"/"RERANKER_"`, composed into `InfraSettings` via `default_factory` so each group also reads `.env` independently.
- Why: a group can be passed whole to its consumer and validated in isolation; adding a field is one line in one class; the prefix makes `.env.example` self-documenting.
- Alternative: keep flat fields on `InfraSettings` — rejected: keeps the provider-era names alive and gives no grouping to hand to consumers.

### Enablement = non-empty `base_url`; no provider enum anywhere
Empty `LLM_BASE_URL` means disabled (placeholders in the analyzer, hindsight raises, `build_llm()` returns `None`); set + empty model raises via `LLMSettings.require_model()`.
- Why: a URL is the one thing every OpenAI-compatible deployment must have, so it is the cheapest truthful on/off signal; the old `LLM_PROVIDER=todo` default was an accident waiting to happen, and the `gpt-4o-mini` fallback targeted the wrong deployment.
- Alternative: keep a `disabled` flag — rejected: two ways to say "off" is worse than one.

### Staged migration: add groups → migrate consumers → delete flat fields
The series first adds the groups with flat env names unchanged (`43d2fec0`), then migrates one consumer per commit (embedder → analyzer → hindsight → reranker → agent), then deletes the flat fields (`491262af`) and renames the integration fixture.
- Why: every commit keeps the suite green and reviewable; no big-bang config cutover.
- Alternative: single cutover commit — rejected: unreviewable and un-bisectable.

### Embedder: `/v1/embeddings`, index-based mapping, width fail-fast
The embedder POSTs the OpenAI shape, maps `data[]` entries to inputs by their `index` field (not array order), sends `Authorization: Bearer` only when a key is set, and verifies 768 dims before persisting.
- Why index mapping: the OpenAI response contract guarantees `index`, not order — the mapping is only genuinely tested if the fake response returns distinct per-index vectors in a shuffled order (`9dbffc5b` proves `out[i][0] == float(i)`).
- Why conditional auth: keyless local endpoints (Ollama) work without a dummy key; keyed endpoints require it.
- Why the width check: silently persisting a different-width vector corrupts the pgvector column for every later search.

### pi-agent inherits rather than duplicates
Compose passes `LLM_MODEL/_BASE_URL/_API_KEY` to the sidecar; when `LLM_BASE_URL` is set the sidecar registers them under its `openai` provider label, otherwise it falls back to local Ollama defaults. Explicit `PI_AGENT_*` overrides still win.
- Why: one source of truth for the chat LLM; the sidecar needs no separate model config in the common case.
- Alternative: sidecar-only variables — rejected: drifts from the backend's model by default.

### Compose defaults mirror the deployment reality
`EMBEDDING_BASE_URL` defaults to `http://ollama:11434/v1` (the opt-in profile service, inside the compose network); `LLM_*` default empty.
- Why: embeddings must work out of the box with `--profile ollama`, while the LLM stays off until deliberately configured — matching the old `todo` default's intent without the accident.

## Risks / Trade-offs

- [**BREAKING**: deployments setting `OLLAMA_BASE_URL`/`LLM_PROVIDER` silently lose them (variables are ignored)] → `.env.example` and `docs/start.md` carry the migration mapping; the old names fail closed (inert), not misrouted.
- [Hindsight requires the LLM when used] → acceptable: it is an analysis feature; the error is explicit ("LLM unavailable") rather than a wrong-protocol attempt.
- [Fixed 768-dim width constrains embedding model choice] → documented in `.env.example`; changing width remains a deliberate store migration.
- [Two sources of model truth if `PI_AGENT_*` overrides diverge from `LLM_*`] → explicit overrides are documented as intentional divergence.

## Migration Plan

Already applied on `main` (`43d2fec0`..`ae6373a5`). For deployments: rename env vars per `.env.example` (`OLLAMA_BASE_URL` → `EMBEDDING_BASE_URL` pointing at the `/v1` endpoint; drop `LLM_PROVIDER`; flat reranker vars → `RERANKER_*`); integration-test environments rename `INTEGRATION_OLLAMA_BASE_URL` → `INTEGRATION_EMBEDDING_BASE_URL`. Rollback = revert the commit range; the flat fields return.

## Open Questions

None.
