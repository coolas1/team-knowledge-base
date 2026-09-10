## Why

The Ask page can become permanently unusable: if any session request fails to
settle, the composer stays disabled with no error and no way out except a page
reload. The user-reported symptom was "the send button cannot be clicked".

Investigation of the live LAN deployment (2026-09-10, `6d899180`) established
that this is **not** a layout or overlay defect. Headless Chrome at 12 viewports
(360→1440) showed the button enabled, hit-testable, and clickable whenever the
session API answered. The defect is a missing failure path:

- `sessionLoading` is initialised `true` and cleared only in a `finally`. The
  textarea is gated by `busy = loading || sessionLoading` and the send button by
  `!query.trim() || sessionLoading`, so a stalled request disables both inputs
  indefinitely. Reproduced by holding `GET /api/agent/sessions` open: status
  stuck on `正在恢复对话`, both controls disabled, no error shown.
- The BFF makes that stall permanent. `routes_agent.py` builds its httpx client
  with `read=None` for **all** `/api/agent/*` routes. That was introduced so SSE
  streams are not cut mid-answer, but the plain-JSON session routes share it, so
  a stalled pi-agent hangs the browser request forever. The absence of a read
  timeout removes the one mechanism that would otherwise release the UI.
- The same lock hits session switching and deletion, which also set
  `sessionLoading`.

The trigger for the underlying stall is not yet pinned down; `listSessions()`
runs on every page load and does serial per-session file I/O on a
single-threaded Node sidecar, so cost grows with session count. This change
makes the failure recoverable regardless of which of those it turns out to be —
a stalled dependency must degrade to a visible error with a retry, never to a
silently dead input.

## What Changes

- **Bounded proxying in the BFF.** Apply an explicit read timeout to the
  agent-proxy JSON routes (session list, detail, create, delete, cancel) so a
  stalled sidecar surfaces as a 504/503 error rather than an unbounded hang. The
  SSE message route keeps unbounded read, but gains a connect bound and must not
  inherit the JSON timeout. A stalled stream stays interruptible by the user.
- **Session load can no longer trap the composer.** A failed or timed-out
  session load clears the loading state, surfaces the error, and leaves the
  composer usable. The user can retry the load or start a new conversation
  without reloading the page.
- **Client-side request deadline.** The SPA bounds its own session requests, so
  a browser-side stall (proxy, network, sleeping laptop) also resolves into an
  error state rather than an indefinite spinner.
- **Loading state is per-operation, not global.** Switching or deleting a
  conversation must not be able to leave the composer disabled if that specific
  operation fails.
- **Deploy-time asset mismatch is reported.** A stale cached `index.html` whose
  hashed bundle no longer exists currently yields a JSON 404 where the browser
  expects JavaScript, so the app never boots. It SHALL fail with a readable
  recovery path instead of a blank page.

## Capabilities

### New Capabilities

- `ask-conversation-ui`: The Ask page's conversation lifecycle — restoring,
  switching, deleting, and sending within agent sessions — including how the
  composer is gated and how session failures surface and recover.

### Modified Capabilities

- `app-deployment`: The requirement that the BFF proxies agent-facing REST
  routes to the sidecar gains a bounded-timeout obligation, distinguishing
  streaming from non-streaming routes.

## Impact

- `src/frontend/webapp/server/routes_agent.py` — split the httpx timeout policy
  between JSON proxy routes and the SSE relay; map upstream timeouts to a
  gateway status.
- `src/frontend/webapp/client/src/pages/AskPage.tsx` — replace the global
  `sessionLoading` gate with a per-operation loading model; add retry/escape
  affordances; stop disabling the composer on failure.
- `src/frontend/webapp/client/src/api/client.ts` — client-side deadline for
  session requests.
- `src/frontend/webapp/server/app.py` — SPA fallback behaviour for a missing
  hashed asset.
- Tests: BFF contract tests for timeout mapping; SPA tests for the failure and
  retry paths. The SPA suite currently runs `environment: 'node'`, so the
  component-level assertions may require a DOM environment.
- No API shape change, no schema or config change, no new dependency.
- Out of scope, deliberately: the `docs/todos.md` items on Hindsight
  conversation-chunk filtering, entity-name splitting, artifact retention,
  repo-level CI, and the untracked `configs/` + `src/engine/memory/`
  directories. The root cause of the underlying sidecar stall is also out of
  scope — this change makes it recoverable, not impossible.
