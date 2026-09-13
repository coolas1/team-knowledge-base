# knowledge-tools Specification

## Purpose
Defines payload bounds for the `tkb_*` MCP tools the agent consumes, so a
single agent turn cannot assemble an unbounded conversation context from tool
output. Bounded tool responses keep prefill costs and latency predictable on
the LLM backend the deployment uses.

## Requirements

### Requirement: Document reading returns bounded content windows

`tkb_get_document` SHALL NOT return a document's full text in a single
response. It SHALL return document metadata, overview, chunk statistics, and
one bounded window of the text, together with the parameters needed to
request further windows (offset/limit style). Each response SHALL stay within
a configured character budget, and the response SHALL state whether more
content remains.

#### Scenario: Reading a long document
- **WHEN** the agent requests a document whose text exceeds the window budget
- **THEN** the response contains one window of the text, the offset used, an indication that more content remains, and the agent can fetch the next window with a follow-up call

#### Scenario: Reading a short document
- **WHEN** the agent requests a document whose text fits within the window budget
- **THEN** the response contains the entire text and indicates no further windows

### Requirement: Document list pages are clamped

`tkb_list_documents` SHALL clamp `page_size` to a server-side maximum. When a
request exceeds the maximum, the response SHALL reflect the effective page
size actually used rather than silently serving the requested size.

#### Scenario: Oversized page request
- **WHEN** the agent requests a page size above the maximum
- **THEN** the server returns at most the maximum number of items and the response reports the effective page size

### Requirement: Deep search evidence is budgeted

`tkb_search_deep` responses SHALL bound each source excerpt and the total
evidence payload size. When evidence exceeds the budget, the response SHALL
be trimmed to fit and SHALL report the trimming so the agent knows the
evidence set is partial.

#### Scenario: Broad search exceeds the evidence budget
- **WHEN** a deep search collects more evidence than the total budget allows
- **THEN** the response includes excerpts trimmed to fit the budget and a marker reporting that trimming occurred

### Requirement: Tool responses declare their payload bounds

Each bounded tool response SHALL be self-describing enough for the agent to
continue reading (window parameters, effective page size, or trimming
markers), without requiring out-of-band knowledge of the configured budgets.

#### Scenario: Agent continues reading without configuration knowledge
- **WHEN** a bounded response indicates more content is available
- **THEN** the response itself carries the parameters or instructions needed to request the remaining content

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
