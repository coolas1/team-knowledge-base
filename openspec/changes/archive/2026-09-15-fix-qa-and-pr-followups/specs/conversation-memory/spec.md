## MODIFIED Requirements

### Requirement: Automatic post-response retention
The system SHALL retain each successfully completed user/assistant turn without requiring the user or model to call a memory tool. Retained content SHALL preserve speaker roles and SHALL record stable session and turn provenance. Retained turn content SHALL be bounded: combined user/assistant text exceeding a configured maximum (default 100,000 characters) SHALL be truncated with an explicit truncation marker rather than retained in full.

#### Scenario: Successful response completes
- **WHEN** the agent successfully completes a response to a user message
- **THEN** the system submits that user/assistant turn for durable conversation-memory retention

#### Scenario: Response is cancelled or fails
- **WHEN** response generation is cancelled or terminates with an error before completion
- **THEN** the system does not retain an incomplete assistant turn

#### Scenario: Oversized turn is retained bounded
- **WHEN** a completed turn contains pasted content far exceeding the configured maximum
- **THEN** the retained memory contains at most the configured maximum of text, ends with a truncation marker, and is retained exactly once

### Requirement: Failure-isolated memory lifecycle
Recall and retention failures SHALL NOT prevent an otherwise valid conversation from continuing. The system SHALL expose failures through structured logs and health or runtime diagnostics without returning sensitive retained content. The client runtime SHALL log every recall or retention failure it swallows, and SHALL report an unavailable memory status distinctly from a queue that has failed jobs.

#### Scenario: Recall service is unavailable
- **WHEN** automatic recall fails or exceeds its configured timeout
- **THEN** response generation continues without recalled context and the failure is recorded diagnostically

#### Scenario: Retention service is unavailable
- **WHEN** a completed turn cannot be retained
- **THEN** the completed answer remains available to the client and the retention failure is recorded diagnostically

#### Scenario: Memory status cannot be determined
- **WHEN** the client runtime cannot reach the memory status operation
- **THEN** it reports the status as unavailable and logs the failure, without reporting it as a failed retention job
