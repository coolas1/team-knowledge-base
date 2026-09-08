## ADDED Requirements

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
