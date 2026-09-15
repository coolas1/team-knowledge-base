## MODIFIED Requirements

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

## ADDED Requirements

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
