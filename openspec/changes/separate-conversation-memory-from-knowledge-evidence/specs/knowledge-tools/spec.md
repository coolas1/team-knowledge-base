## ADDED Requirements

### Requirement: Search tools expose source authority and grouping
Search tool responses SHALL identify the selected route and SHALL expose document evidence and conversation context as distinct groups when both are requested. Existing required source fields SHALL remain available, and new grouping, authority, and provenance fields SHALL be additive.

#### Scenario: Existing document-search client
- **WHEN** an existing client invokes a search without source-routing fields
- **THEN** it receives the existing response shape populated with uploaded-document evidence by default

#### Scenario: Mixed route response
- **WHEN** the agent explicitly invokes a mixed route
- **THEN** the response distinguishes authoritative document evidence from auxiliary conversation context

### Requirement: Deep search invokes optional phases adaptively
Deep search SHALL begin with bounded document lexical and vector retrieval and SHALL invoke query analysis, graph expansion, temporal expansion, and neural reranking only when required by query intent or insufficient evidence. It SHALL be allowed to complete early when configured evidence-sufficiency criteria are met.

#### Scenario: Simple question reaches deep search
- **WHEN** initial bounded retrieval finds sufficient high-confidence evidence for a simple question
- **THEN** deep search returns that evidence without waiting for unnecessary graph, temporal, or LLM phases

#### Scenario: Multi-hop query
- **WHEN** a query requires relationships across documents
- **THEN** deep search invokes the required expansion phases within the remaining total budget

### Requirement: Deep search degradation preserves usable evidence
Each deep-search phase SHALL have a bounded deadline under one monotonic total budget. Optional phase failure SHALL return usable evidence with explicit degradation details. If no usable deep evidence remains, the trusted agent wrapper SHALL attempt at most one bounded fallback path that does not require query-analysis or reranking LLM calls, unless the request was cancelled.

#### Scenario: Neural reranker times out
- **WHEN** lexical/vector evidence exists and neural reranking exceeds its phase deadline
- **THEN** the search returns deterministically ranked evidence marked as degraded

#### Scenario: Deep search has no usable evidence
- **WHEN** deep search times out or becomes unavailable before producing usable evidence
- **THEN** one bounded lightweight fallback is attempted and its origin, outcome, and correlation identifier are reported

#### Scenario: User cancellation
- **WHEN** the user cancels a deep search
- **THEN** pending child work is cancelled and no fallback is started
