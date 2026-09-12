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
