## Context

See proposal.md for the reported symptom and the investigation that ruled out a
layout or overlay cause.

Current state that shapes the approach:

- `src/frontend/webapp/server/routes_agent.py` builds one `httpx.AsyncClient`
  with `timeout=httpx.Timeout(connect=5.0, read=None, write=30.0, pool=5.0)` and
  uses it for both the JSON proxy routes (`_proxy_json`) and the SSE relay
  (`_relay_sse`). `read=None` is required for streaming, but it also removes the
  only bound on the JSON routes.
- `AskPage.tsx` holds a single `sessionLoading` boolean, initialised `true`,
  cleared only in `finally` blocks. It gates the textarea (`busy`) and the send
  button (`sessionLoading`), and is set by restore, switch, and delete alike.
- The SPA test suite is `environment: 'node'` and every existing suite is a
  pure-module test (`session-transcript.test.ts`, `tool-activity.test.ts`,
  `not-found.test.ts`). There is no jsdom or DOM-testing dependency installed.
- The SPA shell is served with `ETag`/`Last-Modified` but **no `Cache-Control`**,
  so browsers apply heuristic freshness and can reuse a stale `index.html` after
  a deploy. A missing hashed asset hits the `/{full_path:path}` catch-all, which
  returns the JSON 404 body `{"detail":"Not Found"}` where the browser expects
  JavaScript.
- No Content-Security-Policy is set anywhere in the server, so an inline script
  in the shell is permissible.

## Goals / Non-Goals

**Goals:**

- No session operation can leave the composer permanently disabled; every one
  settles into success or a visible, retryable error.
- Bound the BFF's non-streaming agent proxy so a stalled sidecar becomes a
  gateway status.
- Keep the answer stream uncut, and keep it interruptible by the user.
- Keep the fix testable within the existing test infrastructure.

**Non-Goals:**

- Root-causing the sidecar stall itself. This change makes the failure
  recoverable regardless of its trigger.
- Changing the agent HTTP API shape, the session data model, or the SPA's
  routing.
- Adding jsdom/@testing-library and a DOM test environment. Not required by the
  chosen decomposition; noted as a residual gap.
- Server-push keepalives on the SSE stream, or resumable streams.

## Decisions

### 1. Bound both hops, not just one

Apply a bounded read timeout at the BFF *and* an independent client-side
deadline in the SPA.

The BFF timeout cannot protect against a stall on the browser↔BFF hop (a proxy
holding the connection, a suspended laptop, dropped Wi-Fi). The client deadline
is the only guarantee that the UI settles, so it is the load-bearing one; the
BFF timeout exists to convert a sidecar stall into a *meaningful status* rather
than a generic client timeout.

*Alternatives considered:* client deadline only (leaves the BFF holding
connexions open indefinitely and reports every stall as a generic timeout with
no server-side diagnosis); BFF timeout only (does not settle the UI when the
browser hop stalls — the original defect persists).

Ordering constraint: the client deadline MUST exceed the BFF timeout, otherwise
the client aborts first and the server's more specific error is never seen.

### 2. Split the httpx timeout policy by route class

Parameterise the client factory so JSON routes get a finite read timeout while
the SSE relay keeps `read=None` with bounded `connect`/`write`/`pool`.

*Alternatives considered:* a single short read timeout everywhere — rejected
because it would truncate long model turns, which is precisely why `read=None`
was introduced; a per-route timeout override at the call site — workable but
easy to forget on a new route, whereas a factory parameter makes the choice
explicit at construction.

The read timeout SHALL be configurable (env var with a conservative default, in
the tens of seconds) because the correct value depends on corpus and model
latency, and a too-aggressive bound would convert working-but-slow operations
into failures.

Upstream timeout maps to `504`; connection failure keeps mapping to `503`. Both
are already-handled error classes, so the mapping stays in `_proxy_json`.

### 3. Replace the global `sessionLoading` with a discriminated operation state

Model the in-flight operation explicitly (restore / switch / delete, with the
target session id where relevant) instead of one boolean. The composer gate then
becomes *only* "a submission is in flight", which is what the spec requires.

*Alternatives considered:* keep the boolean and add a timeout on every `finally`
path — rejected because the boolean is the actual defect: any single failed
operation disables every control, so the failure mode reappears the next time a
new operation forgets to clear it. Making the state impossible to conflate is
the fix; the timeout only bounds how long the wrong state lasts.

Consequence to handle explicitly: the send path currently creates a session when
`sessionRef.current` is unset. With the composer usable during a background
restore, a send could race the restore and create a duplicate session. The send
path MUST resolve the target session deterministically (await the in-flight
restore, or start a new conversation) rather than relying on the composer having
been disabled.

### 4. Extract recovery logic into a pure module for testability

Put the settle/error/retry decision in a plain module (alongside the existing
`session-transcript.ts` pattern) that maps an operation outcome to presentation
state — loading, ready, or error with a retryable flag. The React component
consumes it.

*Alternatives considered:* test through the component with jsdom +
@testing-library — better fidelity, but pulls in a new DOM environment and
dependency set for this change, in a suite that is deliberately `environment:
'node'`. The pure-module split matches the repo's established convention and
covers the decision logic; the residual gap is that the React wiring itself is
not DOM-tested, which is recorded below rather than silently accepted.

### 5. Detect a failed SPA boot with an inline pre-bootstrap guard

Register the failure handler in the shell *before* the bundle is requested, so
it is active when the bundle fails to load. Catch both resource-load errors
(capture phase — resource errors do not bubble) and module-evaluation errors
(bubble phase), plus a load deadline, and reveal a static recovery node
instructing the user to reload.

*Alternatives considered:* an in-bundle `window.onerror` handler — cannot work,
since if the bundle fails to load nothing in it runs, which is the exact case to
handle; a `try/catch` around a dynamic `import()` of the bundle — also viable
and arguably cleaner, but it changes how Vite's entry is emitted and the build
config for a case that a listener covers. The listener is the smaller change.

The guard must be introduced through the client build (a Vite
`transformIndexHtml` hook or the source `index.html`), never hand-edited into
`client/dist/`, which `.dockerignore` excludes from the image context.

## Risks / Trade-offs

- **A too-short read timeout turns working operations into 504s.** → Default
  conservatively, make it configurable, and keep the client deadline above it so
  one knob does not silently mask the other. Verify against the live deployment
  before tightening.
- **Composer usable during a background restore admits a send/restore race
  that may create a duplicate session.** → The send path must resolve the
  target session explicitly; this is called out as its own task and its own
  test, not left to the disabled state.
- **Clearing the loading state on failure can expose a half-restored thread.**
  → Define the failure presentation state explicitly (thread cleared or marked
  stale, error surfaced, retry offered) rather than leaving whatever was
  partially applied.
- **An inline script in the shell is a step away from CSP-hardening later.** →
  Keep it minimal and self-contained so it can be given a nonce or hash if a CSP
  is introduced.
- **The React wiring remains DOM-untested.** → Accepted for this change;
  the decision logic is covered in the pure module. Adding a DOM environment is
  a candidate follow-up, not a silent omission.

## Migration Plan

1. Land the BFF timeout split with its contract tests; this is independently
   safe and immediately stops unbounded hangs.
2. Land the SPA state change and the pure recovery module with its tests.
3. Land the shell boot guard, verifying it in the built image (not just dev),
   since it depends on how Vite emits the entry script.
4. Deploy via the LAN pipeline and verify against the live deployment by
   inducing a stall (hold the sidecar's session route) and confirming the
   composer recovers with a visible, retryable error.

Rollback is the standard pipeline path — revert the SHA and redeploy; no schema,
config, or data migration is involved, so no backup/restore step is required.

## Open Questions

- What actually stalls the sidecar? `listSessions()` runs on every page load and
  performs serial per-session file I/O (snapshot reads, `interruptUnfinished`,
  `SessionManager.open` for ids lacking a snapshot) on a single-threaded Node
  server, so cost grows with session count. Not resolved here: the deliverable is
  that a stall of any origin becomes recoverable. Diagnosing and removing the
  trigger should be its own change, informed by whatever the new error surfaces
  reveal in production.
