# app-deployment Specification

## Purpose

Defines how the knowledge base ships as one app: a single backend process hosting the BFF, the engine, and the plugin, with an agent sidecar and backing services, and with optional model services started only on request.

## Requirements

### Requirement: One backend process serves every surface
The deployable backend SHALL be a single process that serves the SPA, the REST API under `/api`, the MCP endpoint at `/mcp`, and a health endpoint. The engine and the agent plugin SHALL run in-process within that process; no separate engine or MCP service SHALL be required.

#### Scenario: Backend starts
- **WHEN** the backend container starts
- **THEN** one process serves the SPA, `/api/*`, `/mcp`, and `/health`, backed by one in-process engine instance

#### Scenario: REST and MCP share the engine
- **WHEN** a document is uploaded through the REST API and then searched through an MCP tool
- **THEN** both operations act on the same engine and observe the same document

### Requirement: Application wiring happens once, at startup
The backend SHALL construct and wire its components (database schema, engine, optional query service, plugin, LLM client, MCP services, optional graph worker) in a single startup sequence, so every surface shares one consistent engine and configuration. Startup SHALL fail fast on configuration errors (unknown engine implementation, unreachable database).

#### Scenario: Startup completes
- **WHEN** the backend starts with valid configuration
- **THEN** the database schema is initialized, the engine is built, the plugin is loaded, and the MCP services are wired before traffic is served

#### Scenario: Invalid engine selector
- **WHEN** configuration selects an engine implementation that does not exist
- **THEN** startup fails with a clear error instead of serving traffic

### Requirement: Agent sidecar connects over MCP
The agent sidecar (pi-agent) SHALL reach the backend exclusively through its MCP endpoint, and the BFF SHALL proxy agent-facing REST routes to the sidecar. The sidecar holds no model credentials and no direct database access.

#### Scenario: Sidecar uses the app's MCP endpoint
- **WHEN** the sidecar starts in the deployment
- **THEN** it is pointed at the backend's `/mcp` endpoint and performs all knowledge-base work through MCP tools

### Requirement: Ollama is opt-in
The compose deployment SHALL exclude the Ollama service from the default service set; it SHALL start only when the `ollama` profile is enabled. Deployments that use external OpenAI-compatible endpoints SHALL NOT need the Ollama service running.

#### Scenario: Default compose up
- **WHEN** the deployment starts without profiles
- **THEN** the Ollama service is not started

#### Scenario: Ollama profile enabled
- **WHEN** the deployment starts with the `ollama` profile
- **THEN** the Ollama service starts and can serve local models

### Requirement: Development mirrors production
Local development SHALL run the same single backend process as production; the SPA dev server SHALL proxy its API calls to that process rather than to a separate dev backend.

#### Scenario: Developer runs the app locally
- **WHEN** a developer starts the backend (uvicorn) and the SPA dev server
- **THEN** the SPA's `/api` and `/mcp` requests are served by the same backend process that production uses

### Requirement: Uploaded document files persist across restarts
The backend SHALL store uploaded document files at a configured absolute
directory. In the compose deployment that directory SHALL be a named volume
mount, so recreating or redeploying the webapp container MUST NOT lose
uploaded files. The stored files SHALL remain the backing store for
original-file download, content edit, and re-ingest of their documents.

#### Scenario: Files survive a container recreation
- **WHEN** a document is uploaded and the webapp container is recreated (new version deploy)
- **THEN** the document's original file is still present and downloadable, editable, and re-ingestable

#### Scenario: Unconfigured deployment keeps current behavior
- **WHEN** no uploads directory is configured
- **THEN** the backend uses its default uploads location and behaves as before

### Requirement: Agent turn deadline is configured for the backend's latency

The deployment SHALL set the agent sidecar's turn deadline explicitly rather
than relying on the library default, sized above the LLM backend's realistic
worst-case multi-iteration turn, while keeping the tool-timeout hierarchy
validation satisfied.

#### Scenario: Slow turn survives
- **WHEN** an agent turn's LLM iterations take longer in total than the library-default deadline but finish within the configured deadline
- **THEN** the turn completes instead of being aborted for time

#### Scenario: Timeout hierarchy stays valid
- **WHEN** the deployment sets the turn deadline
- **THEN** the deep-tool timeout plus the turn reserve remains below the deadline, as enforced by the sidecar's startup validation

### Requirement: Turn failures log their underlying cause

When an agent turn fails, the sidecar SHALL log the underlying error
(redacted) alongside the failure code, so the cause is diagnosable from
container logs without inspecting session files.

#### Scenario: agent_failed is diagnosable
- **WHEN** a turn fails with the generic agent failure code
- **THEN** the sidecar log contains the underlying error message for that session and turn
