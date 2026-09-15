## Purpose

Defines how the Ask page manages agent conversation sessions — restoring the
most recent one, switching between them, deleting them, and sending messages —
with particular attention to how the composer is gated and how session failures
surface to the user and recover.

## ADDED Requirements

### Requirement: Composer availability reflects the user's own actions

The Ask composer SHALL be interactive whenever the application is idle, and
SHALL NOT be disabled by the state of a background session request. The send
control SHALL be disabled only when there is no submittable message or a message
is actively being sent. While a background operation is in flight the system
SHALL indicate that operation without blocking message composition.

#### Scenario: Sending is in progress
- **WHEN** a message has been submitted and the assistant is responding
- **THEN** the send control is replaced by a stop control and the composer does not accept a second concurrent submission

#### Scenario: A background operation is in flight
- **WHEN** a session is being restored, switched, or deleted
- **THEN** the user can still type in the composer, and the send control is disabled only until there is submittable text

#### Scenario: No message to send
- **WHEN** the composer is empty
- **THEN** the send control is disabled

### Requirement: Session operations fail visibly and recoverably

Every session operation (list, restore, switch, delete) SHALL terminate in a
settled state — success or a reported error — within a bounded time. On failure
the system SHALL clear the operation's loading state, surface an error to the
user, and leave the Ask page usable without a page reload. The system SHALL
offer a way to retry the failed operation.

#### Scenario: Session list request stalls
- **WHEN** the request that lists agent sessions does not complete within the client deadline
- **THEN** the loading state clears, an error is surfaced, and the composer is usable

#### Scenario: Session restore request fails
- **WHEN** restoring the most recent session fails
- **THEN** the failure is surfaced and the user can start a new conversation or retry the restore

#### Scenario: Switching conversations fails
- **WHEN** loading a selected conversation fails
- **THEN** the loading state clears, the error is surfaced, and the composer remains usable

#### Scenario: Retry after a failed load
- **WHEN** the user retries a failed session operation after the dependency recovers
- **THEN** the operation completes and the failure state is cleared

### Requirement: Bounded client requests

Every request the Ask page issues for session operations SHALL be bounded by a
client-side deadline, so that a stalled transport, proxy, or network resolves
into a reported error rather than an indefinite pending state. A deadline
expiry SHALL be distinguishable from a server-reported error in the surfaced
message.

#### Scenario: Transport stalls
- **WHEN** a session request receives no response before the client deadline
- **THEN** the request is aborted and the operation reports a timeout rather than remaining pending

#### Scenario: Server reports an error
- **WHEN** a session request receives an error response
- **THEN** the operation reports the server's error message

### Requirement: Stalled asset delivery is reported

The application shell SHALL NOT present a blank or unusable page when a hashed
static asset cannot be fetched — for example a stale cached document referencing
a bundle removed by a deployment. The system SHALL surface a readable recovery
path instructing the user to reload.

#### Scenario: Hashed asset is missing
- **WHEN** the served document references a static asset that the server does not have
- **THEN** the user sees a readable recovery message rather than an unrendered page

#### Scenario: Network failure loading the entry bundle
- **WHEN** the entry bundle request fails at the network level
- **THEN** the recovery message is presented to the user
