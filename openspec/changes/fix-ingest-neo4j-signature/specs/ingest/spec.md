## ADDED Requirements

### Requirement: Graph write completes and records version state
Under the deployed graph-projection configuration, a full ingest SHALL complete the knowledge-graph write stage — a document whose text extracts and analyzes successfully SHALL reach `indexed` status, not fail after Postgres persistence — and each version's Document node in the graph SHALL record the version number and whether it is the current version.

#### Scenario: Fresh upload indexes under the deployed graph projection
- **WHEN** a supported file is uploaded through the public API on the deployed configuration
- **THEN** the document transitions to `indexed` status with its graph nodes written, rather than failing at the graph-write stage

#### Scenario: Document node carries version properties
- **WHEN** a document version is ingested or superseded
- **THEN** that version's Document node in the graph records its `version_number` and `is_current` values, so a superseded version is marked not current
