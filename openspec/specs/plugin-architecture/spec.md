# plugin-architecture Specification

## Purpose

Defines the agent-facing seam of the app: a manifest-driven plugin (skills, hooks, MCP tools) that runs in-process beside the knowledge-base engine, with approval gates expressed as data so in-process callers and external MCP clients interpret them identically.

## Requirements

### Requirement: Plugin is loaded from a manifest-driven folder
The system SHALL load an agent plugin from a plugin folder containing a `plugin.yaml` manifest declaring the plugin name, version, MCP endpoint, and skill list. Each skill SHALL be a folder providing `skill.yaml` (name, description, inputs) and `skill.py` (the executable skill). When the manifest omits the skill list, the system SHALL discover skills by scanning the plugin's `skills/` folder for `skill.yaml` files. The active plugin SHALL be selected by the `plugin.impl` app configuration key.

#### Scenario: Manifest-listed skills load
- **WHEN** the app starts with `plugin.impl` pointing at a plugin whose manifest lists three skills
- **THEN** all three skills are available with their declared names, descriptions, and input schemas

#### Scenario: Skill list omitted
- **WHEN** a plugin's manifest declares no skill list
- **THEN** every folder under its `skills/` directory containing a `skill.yaml` is loaded as a skill

### Requirement: Skills execute against the engine in-process
Skills SHALL operate on the knowledge-base contract in the same process as the host application. No network transport or HTTP client SHALL sit between a skill and the knowledge base.

#### Scenario: Skill performs a search
- **WHEN** a skill needs knowledge-base data
- **THEN** it invokes the in-process knowledge-base contract directly, without calling the application's own HTTP or MCP endpoints

### Requirement: Approval gates are data, not callbacks
Operations subject to policy SHALL be declared as hook files (YAML) that map an operation name to an approval requirement and a question to present. Evaluating a gated operation SHALL produce a serializable result: either proceed, or needs-approval carrying the question and the pending action. The same result SHALL be interpretable identically by an in-process caller and an external MCP caller, and no evaluation SHALL block on a human callback.

#### Scenario: Gated operation without approval
- **WHEN** a gated operation is requested without prior approval
- **THEN** the caller receives a needs-approval result containing the configured question and the pending action, and the operation is not performed

#### Scenario: Gated operation with approval
- **WHEN** a gated operation is requested with approval
- **THEN** the operation proceeds

#### Scenario: Ungated operation
- **WHEN** an operation with no hook declaration is requested
- **THEN** it proceeds without an approval round-trip

### Requirement: MCP tool surface is served in-process by the host app
The plugin SHALL provide the MCP tool surface, and the host webapp SHALL mount it in-process at `/mcp`. The knowledge-base engine itself SHALL NOT provide an MCP server; MCP is an agent-facing concern owned by the plugin layer.

#### Scenario: MCP client connects through the app
- **WHEN** an MCP client connects to the webapp's `/mcp` endpoint
- **THEN** the plugin's tools are served in the same process that serves the REST API, on the same engine instance

#### Scenario: Engine has no MCP surface
- **WHEN** the engine module is used standalone (e.g. via its CLI)
- **THEN** no MCP server is constructed or exposed by the engine

### Requirement: Document removal is approval-gated on MCP
The `remove_document` MCP tool SHALL be guarded by the remove hook. An unapproved call SHALL return the needs-approval outcome without deleting anything; an approved call SHALL delete the document.

#### Scenario: Unapproved removal request
- **WHEN** `remove_document` is invoked without approval
- **THEN** no document is deleted and a needs-approval result (with the configured question) is returned

#### Scenario: Approved removal request
- **WHEN** `remove_document` is invoked with approval
- **THEN** the document is removed and a success result is returned

### Requirement: Memory MCP tools register only when their service exists
The memory MCP tools (`query_knowledge`, `search_knowledge_fast`, `search_knowledge_deep`) SHALL be registered if and only if the reflective query service is wired (memory capabilities enabled via `engine.memory.*`). When the service is absent, the tools SHALL be absent from the tool list — never registered-but-broken — and the plain `search` tool SHALL fall back to non-reflective recall.

#### Scenario: Memory capabilities disabled
- **WHEN** the app runs with memory capabilities off
- **THEN** the MCP tool list contains no memory tools, and `search` answers via plain recall

#### Scenario: Memory capabilities enabled
- **WHEN** the app runs with memory capabilities on
- **THEN** the memory tools appear in the tool list and operate against the query service
