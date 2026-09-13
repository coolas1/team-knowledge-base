# search-evidence Specification

## Purpose
Defines what evidence the agent-facing search tools return: only evidence
relevant to the query (documents and conversation memories judged by the
same gate), so answers cite matching documents and conversations rather
than unrelated content. Also guarantees documents stay findable through
their metadata when their extracted body text is low quality.

## Requirements

### Requirement: Search tools return only relevant evidence

Search evidence SHALL pass a relevance gate before being returned: a
candidate matched only by keywords SHALL pass a query-term coverage floor
(a configurable fraction of the salient query terms, with a configurable
minimum term count), and candidates without keyword matches SHALL pass the
semantic floor. The gate SHALL apply uniformly to document-derived and
conversation-derived memories — inclusion is decided by relevance to the
query, not by source type. When fewer than the requested number of sources
pass the gate, the response SHALL return fewer sources rather than padding
with filtered candidates.

#### Scenario: Unrelated documents are filtered
- **WHEN** a search returns candidates that match only one minor term of a multi-term query (for example a fermentation recipe matching a control-theory query)
- **THEN** those candidates are absent from the response

#### Scenario: Relevant conversation memory is retained
- **WHEN** a past conversation directly discusses the queried topic
- **THEN** its memories remain eligible and are returned alongside document evidence

#### Scenario: Few sources pass the gate
- **WHEN** only three candidates pass the gate although more were requested
- **THEN** the response contains three sources and reports how many candidates were filtered

### Requirement: Documents remain findable by metadata when body text is noisy

The retrieval representation of a chunk or memory SHALL include the
document's identifying metadata (title, filename, and a bounded clean
overview), so a document whose extracted body text is low quality (for
example a scanned PDF whose extraction is OCR noise) still surfaces when
the query matches its title or summary. Text shown to users (document
reading, evidence excerpts) SHALL remain the original extracted text.

#### Scenario: Scanned paper found by its title
- **WHEN** the agent searches for a topic named in a document's title or summary, and that document's extracted body text is mostly unreadable noise
- **THEN** the document appears among the returned sources

#### Scenario: Reading view is unchanged
- **WHEN** a user opens the document or the agent reads document evidence
- **THEN** the displayed text is the original extraction, not the retrieval representation
