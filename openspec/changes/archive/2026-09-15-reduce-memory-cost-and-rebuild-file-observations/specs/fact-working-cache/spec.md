## Purpose

Define bounded reuse of recently selected facts across memory queries, while preserving visibility, freshness and evidence completeness semantics for callers.

## ADDED Requirements

### Requirement: Selected facts have bounded retention
The system SHALL cache recently selected relevant facts with configurable capacity, idle TTL and context token limits. Defaults SHALL be 256 facts, 1800 seconds idle TTL, and at most 8 facts / 1200 estimated text tokens per preload or expansion. Capacity zero SHALL disable caching. Capacity eviction SHALL remove the least recently used entry without deleting persistent facts.

#### Scenario: Capacity and expiration
- **WHEN** capacity is reached or idle TTL expires
- **THEN** least recently used or expired entries cease to be reusable while authoritative facts remain available to normal search

### Requirement: Cache reuse respects current evidence scope
The system SHALL isolate cached facts by bank, visibility and relevant request filters, validate current access and revision before reuse, and exclude deleted, changed or stale facts. Cache failures SHALL fall back to normal retrieval. Relevant valid cached text and provenance SHALL be visible to the answering model.

#### Scenario: Changed access or source
- **WHEN** a cached fact is deleted, changed, migrated or becomes inaccessible
- **THEN** no subsequent request receives its cached content even if the cache lives in a different worker from the mutation

#### Scenario: Related follow-up
- **WHEN** recent valid facts cover a follow-up question
- **THEN** the model receives their content without repeating expensive retrieval solely to recover them, while missing evidence can still be searched

### Requirement: Observation evidence expansion is selective
Observation search SHALL NOT automatically include all source facts. Expansion SHALL select query-relevant facts within count and token budgets, expose truncation and total source count, and allow further queries. Explicit full-evidence review SHALL remain available through the existing review interface.

#### Scenario: Many source facts
- **WHEN** an observation has more evidence than fits the expansion budget
- **THEN** only the selected subset enters model context and the response reports that more evidence exists
