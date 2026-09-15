## ADDED Requirements

### Requirement: Unhandled API failures return a structured error envelope

Any unhandled exception raised while serving an `/api` route SHALL be
returned to the client as a structured error body carrying `code`,
`message`, `suggestion`, and `retryable`, with a server-error status and
`retryable` true. The response SHALL also carry an identifier for the
failure that appears in the server log alongside the traceback, so a
reported failure can be located after the fact. A route that raises its own
explicit HTTP error MUST keep its existing response shape.

#### Scenario: Route fails unexpectedly

- **WHEN** a route raises an unhandled exception
- **THEN** the client receives a server-error status with a structured body
  naming the failure and stating that retrying is possible, instead of a
  bare server-error response

#### Scenario: Failure can be correlated with the log

- **WHEN** an unhandled exception is returned to a client
- **THEN** the response body carries an identifier that is also present in
  the logged traceback for that failure

#### Scenario: Explicit errors are unchanged

- **WHEN** a route raises an explicit HTTP error with its own detail
- **THEN** the response body and status are exactly as before, so existing
  client behavior is unaffected

#### Scenario: The client can already render the envelope

- **WHEN** the SPA receives the structured envelope
- **THEN** it displays the message and the retry suggestion without any
  client-side change
