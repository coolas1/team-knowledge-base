## Purpose

Defines document-first retrieval and bounded within-document passage selection so clean metadata can discover a relevant file while returned evidence remains grounded in its original content.

## ADDED Requirements

### Requirement: Documents are retrieved before passages
The system SHALL maintain a document-level retrieval representation containing bounded identifying metadata and SHALL use it to select candidate documents before ranking original chunks within those documents.

#### Scenario: Topic appears only in document metadata
- **WHEN** a query matches a document title or overview but no individual chunk contains the same topic phrase
- **THEN** the document remains eligible and the system returns the best available original passage with its document provenance

#### Scenario: Unrelated document has a locally similar chunk
- **WHEN** a chunk is weakly similar but its parent document is not relevant to the query
- **THEN** document-level gating prevents it from displacing passages from relevant documents

### Requirement: Metadata fields remain independently weighted
Title, filename, overview, tags, entities, and original chunk text SHALL remain distinguishable retrieval fields so their weights can be configured and evaluated independently. User-visible excerpts SHALL remain original extracted text.

#### Scenario: Title match and body match differ
- **WHEN** one document matches the title and another matches only a body passage
- **THEN** the system records the contributing fields and ranks them using configured field weights rather than treating repeated metadata as body text

### Requirement: Hierarchical retrieval is bounded and traceable
Document candidate count, per-document chunk count, and final evidence count SHALL be bounded. Search trace SHALL report candidate counts, field contributions, document coverage, duplicate collapse, and applied limits without exposing hidden prompts or sensitive content.

#### Scenario: Large document corpus
- **WHEN** a query runs against the projected-scale corpus
- **THEN** only the bounded document set is searched for passages and trace reports the effective limits
