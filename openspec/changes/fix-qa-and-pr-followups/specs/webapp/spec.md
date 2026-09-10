## Purpose

Defines the BFF's HTTP contract for the document, search, and graph surfaces and the SPA's user-facing behavior on those surfaces: request validation, error and empty states, response shape, and the exclusion of internal (conversation-derived) content from public views.

## ADDED Requirements

### Requirement: Document IDs are validated at the API boundary
Every BFF route taking a document ID as a path or query parameter — including the version-history and version-diff routes — SHALL reject values that are not well-formed UUIDs as a request-validation error (422) before the value reaches storage. A well-formed UUID that does not match a document SHALL continue to produce 404.

#### Scenario: Malformed document ID
- **WHEN** a client requests `/api/documents/not-a-uuid`
- **THEN** the API responds 422 with a validation error, not 500

#### Scenario: Malformed ID on a version route
- **WHEN** a client requests `/api/documents/not-a-uuid/versions` or its diff route
- **THEN** the API responds 422 with a validation error, not 500

#### Scenario: Well-formed but missing document
- **WHEN** a client requests a document ID that is a valid UUID but does not exist
- **THEN** the API responds 404 as before

### Requirement: Document detail failures surface an error state
When loading a document fails (404, 422, or server error), the document detail page SHALL show an error message with a way back to the document list, and SHALL NOT remain in a loading state indefinitely.

#### Scenario: Load failure
- **WHEN** the detail page is opened for an ID the API rejects or does not find
- **THEN** the page renders the failure with a back affordance instead of an eternal loading indicator

### Requirement: Failed-document panel shows the latest error only
When a retry is started on a failed document, the panel SHALL clear the previously displayed stored error so that at most one failure message — the most recent — is visible at a time. The detail page's pending-poll SHALL transition to the failed panel when a processing document reaches `failed`, including a versioned edit whose reindex failed.

#### Scenario: Retry after an earlier failure
- **WHEN** the user triggers 重新处理 on a document showing a stored error and the retry fails
- **THEN** the panel shows only the new retry error, not both errors stacked

#### Scenario: Versioned edit fails to reindex
- **WHEN** the user is watching a document detail page whose new version's background reindex fails
- **THEN** the page leaves the progress/pending state and renders the failed panel with the error

### Requirement: SPA fallback does not mask non-SPA surfaces
The SPA fallback route SHALL serve the SPA shell only for client-side routes. Non-SPA GET paths SHALL receive a JSON 404, and unmatched client-side routes SHALL render an in-app 404 page.

#### Scenario: MCP endpoint is not masked
- **WHEN** a client performs `GET /mcp`
- **THEN** the response is a JSON 404, not the SPA shell

#### Scenario: Unknown client route
- **WHEN** the SPA router receives a path that matches no route
- **THEN** the app renders a 404 page offering navigation back to a known page, instead of a blank screen

### Requirement: Related documents carry their relation type
Search responses SHALL include, for each related document, the relation type connecting it to the result context. The SPA SHALL render related documents without empty placeholder text when rendering that type.

#### Scenario: Related document entry is complete
- **WHEN** a search returns related documents
- **THEN** each entry carries its relation type and the SPA renders `标题 (RELATION)` with a non-empty relation label, or omits the parenthetical when no type exists

### Requirement: Public search and graph exclude conversation-derived content
Default public search results and the public graph view SHALL NOT include content derived from conversation memory: no conversation-derived chunks in search results, and no entity whose every source is a conversation-derived document in the graph view.

#### Scenario: Conversation chunk stays out of search
- **WHEN** a conversation turn has been retained and a query semantically matches its text
- **THEN** the default `/api/search` results contain no chunk from that conversation document

#### Scenario: Conversation-only entity stays out of the graph
- **WHEN** an entity was extracted only from conversation-derived documents
- **THEN** it does not appear in the public graph view or in search related-entity results

### Requirement: Neighbors endpoint honors hops and returns links
The graph neighbors endpoint SHALL apply the requested `hops` value (validated within a bounded range) and SHALL return the links among the returned neighborhood in addition to nodes.

#### Scenario: One-hop neighborhood
- **WHEN** a client requests neighbors with `hops=1`
- **THEN** the response contains exactly the one-hop neighborhood and the links between the returned nodes

#### Scenario: Out-of-range hops
- **WHEN** a client requests `hops=0` or `hops=9`
- **THEN** the endpoint rejects the request with a validation error

### Requirement: Entity relation lists are deduplicated
Relation listings for an entity SHALL contain at most one entry per distinct (relation type, direction, other endpoint) combination, regardless of how many parallel edges produced it.

#### Scenario: Parallel edges collapse
- **WHEN** an entity has the same relation to the same target recorded more than once
- **THEN** the entity's relation list shows it once

### Requirement: Content editing is markdown-only by explicit error
Updating document content SHALL be rejected with 400 and a reason naming the actual cause when the target document exists but is not editable (not a markdown document), instead of reporting the document as nonexistent.

#### Scenario: Edit a PDF's content
- **WHEN** a client sends a content update for an existing non-markdown document
- **THEN** the API responds 400 with a reason like "仅支持 Markdown 文档", not 404

### Requirement: Graph nodes have a usable pointer hit area
The graph view SHALL paint a pointer interaction area for each node that is at least twice the node's rendered radius, so nodes remain clickable at high zoom-out densities.

#### Scenario: Clicking near a dense node
- **WHEN** a user clicks within the enlarged hit area of a node in a 1000+ node graph
- **THEN** the node is selected and its detail panel opens
