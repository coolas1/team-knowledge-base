## Why

Model configuration was scattered across provider-era flat variables (`OLLAMA_BASE_URL`, `LLM_PROVIDER`, flat reranker fields) and every engine component carried dual code paths (an Ollama-native protocol plus an OpenAI-compatible one) dispatched on a provider switch. Since every endpoint in use — including local Ollama — serves OpenAI-compatible APIs, the provider axis added config surface, dead code, and silent failure modes: `LLM_PROVIDER=todo` disabled the LLM by accident, an empty `LLM_MODEL` silently fell back to `gpt-4o-mini` (targeting the wrong deployment), and each component re-implemented its own "am I configured?" logic.

## What Changes

- Introduce three settings groups in `config/settings.py`, each a pydantic-settings sub-model with its own env prefix: `LLM_*` (chat/analysis LLM), `EMBEDDING_*` (embeddings), `RERANKER_*` (reranker), composed into `InfraSettings`.
- Make enable/disable semantics explicit and uniform: an empty `LLM_BASE_URL` disables the LLM (analyzer degrades to placeholder results, hindsight raises); an enabled LLM with an empty `LLM_MODEL` is a hard configuration error. No silent model-name fallbacks.
- Point the embedder at the OpenAI `/v1/embeddings` shape exclusively (Ollama-native `/api/embed` removed): responses mapped to inputs **by index**, Bearer auth only when an API key is set, and a fail-fast check that the model emits the stored 768-dim vector width.
- Make the analyzer, hindsight memory providers, and reranker read their settings group directly; delete the analyzer's Ollama-native call path, hindsight's native chat path, and the `LLM_PROVIDER` dispatch.
- Gate the agent's `build_llm()` on `LLM_BASE_URL` (was `LLM_PROVIDER=todo`); enabled-without-model raises instead of falling back.
- Let the pi-agent sidecar inherit the shared `LLM_MODEL/_BASE_URL/_API_KEY` when `LLM_BASE_URL` is set (registered under its OpenAI provider label), falling back to local Ollama defaults otherwise.
- Delete the flat provider-era fields (`OLLAMA_BASE_URL`, `LLM_PROVIDER`, flat reranker vars); rename the integration-test fixture variable `INTEGRATION_OLLAMA_BASE_URL` → `INTEGRATION_EMBEDDING_BASE_URL`. **BREAKING** for deployments still setting the old variables.
- Wire compose: `EMBEDDING_*`/`LLM_*`/`RERANKER_*` pass through to the webapp service; the default embedding endpoint points at the opt-in ollama profile's `/v1`; `LLM_*` defaults are empty (disabled unless configured); the provider variables are gone.
- Document the OpenAI-compatible-only model in `.env.example`, `docs/start.md`, and the README.

## Capabilities

### New Capabilities
- `model-config`: How LLM, embedding, and reranker endpoints are configured — three env-prefixed settings groups, OpenAI-compatible endpoints only, explicit enable/disable semantics, and fail-fast misconfiguration errors.

### Modified Capabilities

None.

## Impact

- `config/settings.py` — `LLMSettings` / `EmbeddingSettings` / `RerankerSettings` sub-models; flat model fields removed.
- `src/engine/components/` — `embedder.py` (OpenAI `/v1/embeddings`, index mapping, width check), `analyzer.py` (OpenAI-compatible only), `reranker.py` (reads the settings group).
- `src/engine/hindsight_components/providers.py` — native Ollama chat path deleted.
- `src/agent/llm.py` — `build_llm()` gating on `LLM_BASE_URL`.
- `src/extensions/pi-agent/` — config inheritance of the shared LLM group.
- `docker-compose.yml`, `.env.example`, `docs/start.md`, `README.md` — env surface and defaults.
- Tests — `tests/config/test_settings.py`, `tests/engine/test_embedder.py`, `test_analyzer.py`, `test_reranker.py`, `tests/agent/test_llm.py`, hindsight provider tests, pi-agent config tests; integration fixtures renamed.
- Retroactive note: this change documents work already applied to `main` (commits `43d2fec0`..`ae6373a5`, twelve commits). The artifacts record what landed; they do not gate it.
