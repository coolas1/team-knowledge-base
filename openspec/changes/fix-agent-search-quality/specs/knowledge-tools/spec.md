## ADDED Requirements

### Requirement: Search tool responses are wholly bounded

Every search tool response (`tkb_search`, `tkb_search_fast`,
`tkb_search_deep`) SHALL keep its entire serialized payload within a
configured character budget, counting all fields together — evidence text,
source metadata, related entities, and trace — not just the per-excerpt
evidence. When the assembled response exceeds the budget, the system SHALL
drop or trim the lowest-ranked evidence first and the response SHALL report
that payload trimming occurred.

#### Scenario: Two searches cannot assemble an oversized context
- **WHEN** the agent runs two searches that would each return a payload near the per-excerpt evidence budget with metadata, entities, and trace on top
- **THEN** each response stays within the whole-response budget on its own

#### Scenario: Payload trimming is reported
- **WHEN** a response's assembled fields exceed the whole-response budget
- **THEN** the response reports that trimming occurred and how many sources were kept or dropped

### Requirement: Search responses do not duplicate evidence

A search tool response SHALL NOT include the same evidence text more than
once. Where a response structure carries both per-source evidence and a
grouped-evidence view, the response SHALL keep the per-source form and omit
or compact the duplicate, staying within the whole-response budget.

#### Scenario: Evidence appears once per response
- **WHEN** a search response contains sources and a grouped-evidence structure covering the same recalled items
- **THEN** the evidence text is not repeated verbatim in both structures
