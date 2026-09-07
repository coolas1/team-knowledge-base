## 1. Settings groups

- [x] 1.1 Add `LLMSettings` / `EmbeddingSettings` / `RerankerSettings` sub-models (env prefixes `LLM_`/`EMBEDDING_`/`RERANKER_`) composed into `InfraSettings`, flat env names unchanged during migration; verify `tests/config/test_settings.py` covers each group's fields, defaults, and `.env` loading (`43d2fec0`).

## 2. Engine consumers migrate to OpenAI-compatible-only

- [x] 2.1 Rework the embedder to the OpenAI `/v1/embeddings` shape (model/API key via `EMBEDDING_*`, Bearer only when a key is set, index-based response mapping, fail-fast 768-dim width check); verify embedder tests cover mapping, auth conditionality, and width mismatch (`fea1bc10`).
- [x] 2.2 Harden the embedder test with distinct per-index vectors in a shuffled fake response so index-based mapping is genuinely proven; verify `out[i][0] == float(i)` assertion (`9dbffc5b`).
- [x] 2.3 Delete the analyzer's Ollama-native path and `LLM_PROVIDER` dispatch; disabled (empty `LLM_BASE_URL`) yields placeholders, enabled-without-model raises via `require_model()`; verify analyzer tests cover both states (`5279e751`).
- [x] 2.4 Delete hindsight's native Ollama chat path and `num_predict` guard; the OpenAI-compatible path is the only one; verify hindsight provider tests pass against the single path (`d7e5cf34`).
- [x] 2.5 Move the reranker to the `RERANKER_*` settings group with unchanged provider semantics; verify reranker tests cover `none`/`http` behavior (`be36a4e8`).

## 3. Agent and sidecar

- [x] 3.1 Gate `build_llm()` on `LLM_BASE_URL` (replacing `LLM_PROVIDER=todo`); enabled with an empty model raises instead of falling back to a default model name; verify `tests/agent/test_llm.py` covers disabled, enabled, and misconfigured states (`c7815a73`).
- [x] 3.2 Make pi-agent inherit the shared `LLM_MODEL/_BASE_URL/_API_KEY` when `LLM_BASE_URL` is set (registered as its `openai` provider label), falling back to local Ollama otherwise, with explicit `PI_AGENT_*` overrides winning; verify pi-agent config tests cover inheritance, fallback, and override precedence (`a91a2b70`).

## 4. Flat-field removal and deployment wiring

- [x] 4.1 Delete the flat provider-era fields (`OLLAMA_BASE_URL`, `LLM_PROVIDER`, flat reranker vars) with all consumers reading the sub-models; rename `INTEGRATION_OLLAMA_BASE_URL` → `INTEGRATION_EMBEDDING_BASE_URL` in the integration fixtures; verify settings tests assert the old names are inert and the suite is green (`491262af`).
- [x] 4.2 Wire compose: `EMBEDDING_*`/`LLM_*`/`RERANKER_*` pass through to the webapp service, embedding default points at the ollama-profile `/v1`, `LLM_*` default empty, provider variables gone; verify `docker compose config` renders the intended defaults and passthrough (`5c62ac75`).
- [x] 4.3 Update `.env.example`, `docs/start.md` (embedding `.env` block), and README to the OpenAI-compatible-only surface with the migration mapping; verify documented variable names match `config/settings.py` (`1e7abb04`).
- [x] 4.4 Ruff-format the series' files (trailing newlines, line length); verify `uv run ruff check` is clean (`ae6373a5`).

## 5. Verification

- [x] 5.1 Run `uv run ruff check` and `uv run pytest` (full suite green), plus `npm test` for the pi-agent config changes; confirm no references to the deleted flat fields remain (`grep -rn "LLM_PROVIDER\|OLLAMA_BASE_URL" src/ config/ docker-compose.yml .env.example` is empty).
- [x] 5.2 Validate the change with `openspec validate model-config --strict` and confirm the twelve landed commits contain only this change's files.
