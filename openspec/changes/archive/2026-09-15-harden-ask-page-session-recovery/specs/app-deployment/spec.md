## MODIFIED Requirements

### Requirement: Agent sidecar connects over MCP

The agent sidecar (pi-agent) SHALL reach the backend exclusively through its MCP endpoint, and the BFF SHALL proxy agent-facing REST routes to the sidecar. The sidecar holds no model credentials and no direct database access.

The BFF SHALL apply a bounded read timeout to non-streaming agent-proxy routes, so that an unresponsive sidecar produces a gateway error status rather than an indefinitely pending client request. Routes that relay a streaming response SHALL NOT be bounded by that read timeout, and SHALL accept a bounded request body with a bounded connection attempt so a request that streams no data still settles.

#### Scenario: Sidecar uses the app's MCP endpoint
- **WHEN** the sidecar starts in the deployment
- **THEN** it is pointed at the backend's `/mcp` endpoint and performs all knowledge-base work through MCP tools

#### Scenario: Sidecar does not respond to a non-streaming request
- **WHEN** a non-streaming agent-proxy route receives no response from the sidecar within the configured read timeout
- **THEN** the BFF returns a gateway error status to the caller instead of holding the request open

#### Scenario: Sidecar is unreachable
- **WHEN** the BFF cannot establish a connection to the sidecar
- **THEN** the BFF returns a service-unavailable status

#### Scenario: Streaming response is not truncated
- **WHEN** a message route relays a streaming response that pauses between events for longer than the non-streaming read timeout
- **THEN** the stream remains open and continues to deliver events
