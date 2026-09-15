# model-config Specification

## Purpose

Defines how the app's model endpoints (chat/analysis LLM, embeddings, reranker) are configured: three env-prefixed settings groups, OpenAI-compatible APIs exclusively, and explicit enable/disable semantics with fail-fast misconfiguration errors.

## Requirements

### Requirement: Model configuration lives in three env-prefixed groups
The system SHALL expose model configuration through three independent settings groups, each reading its own environment-variable prefix: the chat/analysis LLM (`LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`), embeddings (`EMBEDDING_BASE_URL`, `EMBEDDING_MODEL`, `EMBEDDING_API_KEY`), and the reranker (`RERANKER_PROVIDER`, `RERANKER_MODEL`, `RERANKER_BASE_URL`, `RERANKER_API_KEY`). The provider-era variables (`OLLAMA_BASE_URL`, `LLM_PROVIDER`, flat reranker variables) SHALL NOT be recognized as configuration.

#### Scenario: Group variables configure their consumers
- **WHEN** `EMBEDDING_BASE_URL`/`EMBEDDING_MODEL`/`EMBEDDING_API_KEY` are set
- **THEN** the embedding client is configured entirely from those values

#### Scenario: Legacy provider variables are inert
- **WHEN** a deployment still sets `OLLAMA_BASE_URL` or `LLM_PROVIDER`
- **THEN** neither variable affects any model consumer

### Requirement: LLM enablement is explicit and fails fast
An empty `LLM_BASE_URL` SHALL disable the LLM: document analysis stores placeholder results (as before) and hindsight reports the LLM as unavailable. When `LLM_BASE_URL` is set, an empty `LLM_MODEL` SHALL raise a configuration error that names both variables. The system SHALL NOT silently substitute a default model name.

#### Scenario: LLM disabled
- **WHEN** the app runs with an empty `LLM_BASE_URL`
- **THEN** ingestion completes with placeholder analyses and the agent LLM is not built

#### Scenario: Enabled without a model
- **WHEN** `LLM_BASE_URL` is set and `LLM_MODEL` is empty
- **THEN** configuration raises a clear error instead of falling back to a default model

### Requirement: Embeddings are always on, OpenAI-compatible, and width-checked
The embedder SHALL call an OpenAI-compatible `/v1/embeddings` endpoint and SHALL have no disable switch (embeddings are load-bearing for search). Responses SHALL be mapped to their inputs by the index field, not by array order. The stored vector width is fixed at 768; an endpoint returning a different width SHALL fail the ingest with a clear error rather than persisting unusable vectors.

#### Scenario: Response order differs from input order
- **WHEN** the endpoint returns embedding entries in a different order than the inputs
- **THEN** each input is paired with its own embedding, by index

#### Scenario: Wrong vector width
- **WHEN** the configured model returns vectors that are not 768-dimensional
- **THEN** ingestion fails fast with an error naming the mismatch

#### Scenario: Authentication is conditional
- **WHEN** an API key is configured, requests carry Bearer authentication; when it is empty, requests carry none
- **THEN** keyless local endpoints accept the requests and keyed cloud endpoints reject keyless ones

### Requirement: All model consumers speak OpenAI-compatible APIs only
The analyzer, the hindsight memory providers, the agent LLM builder, and the reranker SHALL use their settings group directly and SHALL NOT dispatch on a provider switch or implement native provider protocols.

#### Scenario: Analyzer with the LLM enabled
- **WHEN** documents are analyzed with a configured LLM
- **THEN** analysis calls the OpenAI-compatible chat-completions endpoint; no native provider path exists

#### Scenario: Hindsight with the LLM disabled
- **WHEN** hindsight runs with an empty `LLM_BASE_URL`
- **THEN** it reports the LLM as unavailable rather than attempting another protocol

#### Scenario: Agent LLM builder
- **WHEN** the agent's LLM is built with an empty `LLM_BASE_URL`
- **THEN** no LLM client is constructed; with `LLM_BASE_URL` set and an empty model, construction raises

### Requirement: Reranker selects its implementation by provider
The reranker SHALL read its configuration from the reranker settings group, with `RERANKER_PROVIDER` selecting `none` (default; vector top-K only), `http` (external rerank API), or `local` (in-process model requiring the optional extra).

#### Scenario: Reranker disabled
- **WHEN** `RERANKER_PROVIDER=none`
- **THEN** search returns vector-ranked results without a reranking stage

#### Scenario: Reranker configured
- **WHEN** `RERANKER_PROVIDER=http` with a base URL and model
- **THEN** search reranks candidates through the configured API

### Requirement: The agent sidecar inherits the shared LLM configuration
The pi-agent sidecar SHALL inherit the shared `LLM_MODEL`/`LLM_BASE_URL`/`LLM_API_KEY` values when `LLM_BASE_URL` is set, using them as its OpenAI-compatible provider; when it is empty, the sidecar falls back to its local default model service.

#### Scenario: Shared LLM configured
- **WHEN** the deployment sets `LLM_BASE_URL` and does not override the sidecar's own model variables
- **THEN** the sidecar uses the shared LLM endpoint and model

#### Scenario: Shared LLM unconfigured
- **WHEN** `LLM_BASE_URL` is empty
- **THEN** the sidecar falls back to its local default model service

### Requirement: Compose passes the model groups through
The webapp compose service SHALL pass the existing LLM, embedding and reranker groups and the independent image settings through to their consumers. Image generation SHALL default to disabled. The default embedding endpoint SHALL point at the opt-in Ollama profile service over the compose network; LLM defaults SHALL be empty; no legacy provider-switch variables SHALL be set. Deployment validation SHALL inspect both webapp and Pi effective model configuration after container recreation without exposing credentials.

#### Scenario: Compose defaults render
- **WHEN** compose renders without model overrides
- **THEN** embeddings point at Ollama, LLM is disabled and image generation is disabled

#### Scenario: Overrides pass through
- **WHEN** .env sets an external embedding endpoint
- **THEN** the container receives that value unchanged

#### Scenario: Existing container has stale configuration
- **WHEN** .env changes from DeepSeek to Ark but the container retains its previous environment
- **THEN** verification reports the mismatch and SHALL NOT claim the model switch succeeded

### Requirement: Image model configuration is independent
The system SHALL provide IMAGE_PROVIDER, IMAGE_BASE_URL, IMAGE_MODEL and IMAGE_API_KEY independently of LLM settings. Enabled image generation MUST reject missing configuration and unsupported providers before dispatch. The system SHALL NOT silently substitute a model, inherit a text credential or fall back to a different billing endpoint. Existing text consumers SHALL remain OpenAI-compatible.

#### Scenario: Text model changes
- **WHEN** the shared LLM model changes
- **THEN** image generation configuration and existing embeddings remain unchanged

#### Scenario: Image configuration is incomplete
- **WHEN** image generation is enabled without a model or credential
- **THEN** a clear configuration error is returned before any provider request

### Requirement: Memory reasoning policy is separate from interactive reasoning
The system SHALL offer an explicit auto/disabled/enabled reasoning policy for memory generation, independent of interactive agent reasoning. Supported Ark memory requests SHALL honor the configured policy for summaries, fact extraction, consolidation and mental model refresh. Unsupported explicit policies MUST fail clearly rather than being silently ignored. Effective policy changes SHALL invalidate generation-cache reuse without deleting existing memories.

#### Scenario: Non-thinking memory generation
- **WHEN** supported Ark memory generation is configured with disabled reasoning
- **THEN** the provider request disables reasoning while interactive settings remain unchanged

#### Scenario: Strategy changes
- **WHEN** a persisted summary was produced under another effective reasoning policy
- **THEN** it is not reused as a matching generation-cache result

### Requirement: Provider verification reports measured outcomes
Configuration verification SHALL distinguish endpoint reachability, authentication, model availability, structured output, tool calls, streaming, visual input and billing evidence. Reports MUST record actual model identity and provider usage where supplied, label missing usage or AFP as unknown, redact credentials, and enforce a bounded test budget.

#### Scenario: JSON succeeds without billing evidence
- **WHEN** a model returns a valid JSON response but no subscription deduction evidence
- **THEN** verification reports JSON success without claiming confirmed subscription deduction or zero cost

#### Scenario: Production configuration smoke test
- **WHEN** the Ark configuration is declared operational
- **THEN** both container configuration checks and an isolated synthetic memory-chain test have succeeded without modifying unrelated memories
