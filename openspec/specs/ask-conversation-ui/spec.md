# ask-conversation-ui Specification

## Purpose

Defines how the Ask page manages agent conversation sessions — restoring the
most recent one, switching between them, deleting them, and sending messages —
with particular attention to how the composer is gated and how session failures
surface to the user and recover.

## Requirements

### Requirement: Sending works from insecure browser contexts

The Ask page SHALL be usable from every origin the deployment serves, including
plain-HTTP non-localhost origins that browsers classify as insecure contexts.
The send path SHALL NOT depend on any Web API that is only exposed in secure
contexts (such as `crypto.randomUUID`). A message submitted from an insecure
context SHALL produce the same client-generated message identifier format as a
secure context, and SHALL reach the server and stream an answer exactly as it
does from a secure context.

#### Scenario: Send from a plain-HTTP LAN origin
- **WHEN** the SPA is served over plain HTTP on a non-localhost host and the user submits a question
- **THEN** the message is delivered to the backend, the conversation shows the user message and the streamed answer, and no error is raised for a missing secure-context-only API

#### Scenario: Client message identifiers keep their format
- **WHEN** a message is submitted from an insecure context
- **THEN** the client-generated message identifier is a UUID string indistinguishable in format and uniqueness from one generated in a secure context
