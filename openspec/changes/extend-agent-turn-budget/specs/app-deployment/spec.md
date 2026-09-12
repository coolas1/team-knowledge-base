## ADDED Requirements

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
