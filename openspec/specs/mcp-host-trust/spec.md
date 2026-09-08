# mcp-host-trust Specification

## Purpose
Defines which HTTP Host headers the knowledge base's MCP endpoint accepts,
keeping DNS-rebinding protection active while allowing localhost and
deployment-internal clients to connect without networking workarounds.

## Requirements

### Requirement: Localhost Host headers accepted
The MCP endpoint SHALL accept requests whose Host header is `localhost`,
`127.0.0.1`, or `[::1]`, on any port.

#### Scenario: Local client connects
- **WHEN** a client on the same host connects to the MCP endpoint using a localhost Host header
- **THEN** the connection is accepted and the MCP handshake proceeds

### Requirement: Deployment-internal Host headers accepted
The MCP endpoint SHALL accept requests whose Host header matches the
deployment's internal service names — `webapp:8000` and
`team-kb-webapp:8000` — so agent sidecars deployed in the same container
network can reach the MCP endpoint without host networking.

#### Scenario: In-network sidecar connects
- **WHEN** a sidecar on the deployment's container network connects via the backend's internal service URL
- **THEN** the request is accepted and MCP tools are usable by the sidecar

#### Scenario: Unknown Host header still rejected
- **WHEN** a request arrives with a Host header that is neither localhost nor a deployment-internal service name
- **THEN** the request is rejected and DNS-rebinding protection remains active
