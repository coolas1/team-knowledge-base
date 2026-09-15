## Purpose

Ensure deep knowledge retrieval returns bounded, diagnosable evidence under slow or failing dependencies while preserving enough turn time for fallback and answer synthesis as the knowledge base grows.

## ADDED Requirements

### Requirement: Bounded deep-search deadline
The system SHALL apply one monotonic total deadline to a deep search and SHALL bound every external-model, embedding, and retrieval phase by both its configured phase limit and the remaining total time. The default service-side deep-search budget SHALL be shorter than the Pi deep-tool deadline.

#### Scenario: Upstream model stalls
- **WHEN** query analysis or neural reranking does not respond within its phase deadline
- **THEN** the phase SHALL be cancelled and the deep search SHALL either return degraded evidence within the total deadline or return a typed deep-search timeout

#### Scenario: Deep-search total budget expires
- **WHEN** the total deep-search deadline expires before usable evidence is available
- **THEN** all unfinished work SHALL be cancelled and the caller SHALL receive a typed timeout rather than an indefinite or generic transport failure

### Requirement: Graceful partial-result degradation
The system SHALL treat query analysis, graph expansion, temporal retrieval, and neural reranking as optional enhancement phases. A failed optional phase MUST NOT discard useful evidence from completed retrieval phases, and the result SHALL identify every skipped, failed, or timed-out phase.

#### Scenario: Query analysis is unavailable
- **WHEN** query analysis fails or times out but embedding or keyword retrieval remains available
- **THEN** the system SHALL continue without extracted entities or time bounds and return evidence from the available arms with degradation metadata

#### Scenario: Reranking is unavailable
- **WHEN** neural reranking fails or times out after candidates have been retrieved
- **THEN** the system SHALL rank candidates with the deterministic fused retrieval score and mark neural reranking as degraded

#### Scenario: One retrieval arm fails
- **WHEN** one of semantic, keyword, graph, or temporal retrieval fails or times out and another arm returns useful candidates
- **THEN** the system SHALL return evidence from the successful arms and identify the failed arm without reporting the whole search as failed

#### Scenario: No arm returns usable evidence
- **WHEN** all usable retrieval arms fail, time out, or return no evidence
- **THEN** the system SHALL return a typed unavailable or timeout outcome that distinguishes empty knowledge coverage from dependency failure

### Requirement: Authoritative cancellation
The system SHALL propagate user, client-disconnect, and turn-deadline cancellation through the MCP request to every active deep-search phase. Cancellation SHALL take precedence over graceful degradation and SHALL NOT leave model requests or database work running in the background.

#### Scenario: User cancels during reranking
- **WHEN** the user cancels while a reranking request and retrieval tasks are active
- **THEN** the system SHALL cancel all active work promptly, return a cancellation outcome, and SHALL NOT convert that cancellation into a degraded success

### Requirement: Coordinated Pi turn budget
The Pi runtime SHALL reserve time for fallback and final answer synthesis. It SHALL reject invalid timeout configuration at startup and SHALL cap each deep-tool invocation to the lesser of its configured limit and the current turn time remaining before the reserve.

#### Scenario: Invalid deadline hierarchy
- **WHEN** the configured deep-tool deadline plus the configured turn reserve is greater than or equal to the Agent turn limit
- **THEN** Pi initialization SHALL fail with an error naming the conflicting settings and the required ordering

#### Scenario: Deep search starts late in a turn
- **WHEN** `tkb_search_deep` starts after earlier tool calls have consumed part of the turn
- **THEN** its effective timeout SHALL use the remaining turn budget while preserving the configured reserve

#### Scenario: Default deployment values
- **WHEN** no timeout overrides are configured
- **THEN** the deep-tool limit SHALL be 60 seconds, the Agent turn limit SHALL be 180 seconds, and the turn reserve SHALL be at least 60 seconds

### Requirement: Single bounded fast fallback
After a typed deep-search timeout or a degraded result with no usable evidence, Pi SHALL allow at most one fast-search fallback for that deep-search invocation when sufficient turn time remains. The fallback SHALL be visible in tool activity and SHALL consume the same turn's budgets.

#### Scenario: Deep search times out with time remaining
- **WHEN** deep search times out and enough time remains before the answer reserve
- **THEN** Pi SHALL attempt `tkb_search_fast` once, label the returned evidence as fallback evidence, and continue answer synthesis

#### Scenario: Fallback also fails
- **WHEN** the one fast fallback fails, times out, or returns no evidence
- **THEN** Pi SHALL stop retrieval for that invocation and report the evidence limitation without retrying deep or fast search again automatically

#### Scenario: Cancellation caused the failure
- **WHEN** deep search ends because of user cancellation or client disconnect
- **THEN** Pi SHALL NOT start a fallback search

### Requirement: Correlated deep-search diagnostics
Every deep search SHALL have a correlation identifier and SHALL record bounded phase start, completion, elapsed time, outcome, total elapsed time, and degradation reasons. Diagnostics SHALL exist for success, degraded success, timeout, upstream error, and cancellation without logging query text, evidence content, credentials, or model responses by default.

#### Scenario: Successful degraded response
- **WHEN** a deep search returns evidence after an optional phase timeout
- **THEN** its additive trace fields and structured logs SHALL share the correlation identifier and identify the phase timeout and fallback ranking method

#### Scenario: Search fails before returning a response trace
- **WHEN** a deep search fails or is cancelled before an MCP result can be produced
- **THEN** server logs SHALL still contain the correlation identifier, terminal outcome, completed phase timings, and sanitized failure category

### Requirement: Bounded indexed keyword retrieval
Keyword retrieval SHALL use an indexed, bounded candidate selection that supports the existing English word and CJK bigram matching semantics before full memory records are materialized. Its application-side scoring work SHALL be capped independently of the total number of stored memories.

#### Scenario: Mixed Chinese and English query
- **WHEN** indexed memories contain English words and CJK text matching the normalized query tokens
- **THEN** keyword retrieval SHALL return and rank matching candidates using both token forms without requiring an application-side full-table scan

#### Scenario: Large memory collection
- **WHEN** the store contains at least 30,000 active memory units
- **THEN** a keyword search SHALL materialize no more than its configured candidate cap for application-side scoring and SHALL preserve the requested result limit

#### Scenario: Existing deployment is migrated
- **WHEN** an existing database does not yet have complete indexed keyword tokens
- **THEN** operators SHALL be able to add and backfill the index without losing stored memories, and indexed retrieval SHALL not be enabled until completeness validation succeeds

### Requirement: Bounded LLM reranking input
Deep search SHALL cap the number of candidates, per-candidate text length, and total text supplied to neural reranking. Truncation SHALL be deterministic and visible in diagnostics.

#### Scenario: Candidate text exceeds the reranking budget
- **WHEN** retrieved candidates exceed any reranking input bound
- **THEN** the system SHALL truncate the reranking input deterministically, preserve source identifiers, and record that truncation occurred

### Requirement: Compatible deep-search contract
The public `tkb_search_deep` input contract SHALL remain `query` plus optional `top_k`. Successful responses MAY add trace, degradation, and fallback metadata, but existing evidence fields SHALL retain their meaning.

#### Scenario: Existing client calls deep search
- **WHEN** an existing client calls `tkb_search_deep` without awareness of the new metadata
- **THEN** it SHALL continue to receive the existing evidence fields and SHALL be able to ignore all additive fields

