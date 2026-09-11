## Why

The SPA is deployed over plain HTTP on the LAN (e.g. `http://10.201.110.190:8000`).
Browsers classify every origin that is not HTTPS and not `localhost` as an insecure
context, where `crypto.randomUUID` is `undefined`. The Ask page's send path calls it
(`src/frontend/webapp/client/src/pages/AskPage.tsx:299`) after clearing the composer but
before any network request or error handling, so on the LAN deployment every send
clears the input and silently swallows the message — an uncaught promise rejection via
`void run()` with no error banner and no server-side activity. Reproduced live
2026-09-11 (QA report: `bench/qa-ask-latency-2026-09-11/REPORT.md`, addendum);
localhost testing never sees it because `localhost` is a secure context.

## What Changes

- Add a small client utility that produces a UUID: uses `crypto.randomUUID` when
  available and falls back to an RFC 4122 v4 UUID built from
  `crypto.getRandomValues` (which IS available in insecure contexts).
- Replace the single call site in the Ask page send path (`AskPage.tsx:299`) with the
  helper. No other call sites exist (`crypto.*` appears exactly once in `client/src`).
- Unit tests for the fallback path (randomUUID absent) and for the generated format.
- Non-goals: surfacing pre-`try` failures in `run()` more loudly, and the separate
  ask-latency dead-zone issue documented in the QA report.

## Capabilities

### New Capabilities

- `ask-conversation-ui`: extends the capability being introduced by the in-flight
  `harden-ask-page-session-recovery` change (no main spec exists yet; both deltas merge
  on archive). This change adds the requirement that the send path must not depend on
  secure-context-only Web APIs, so asking works from any origin the deployment serves.

### Modified Capabilities

(none)

## Impact

- **Code:** `src/frontend/webapp/client/src/` — new util module + tests, one-line change
  in `AskPage.tsx`. No BFF/engine/agent changes; `clientMessageId` remains a UUID
  string over the wire, so the server contract is unchanged.
- **Validation:** `cd src/frontend/webapp/client && npm test` (SPA tests);
  `uv run ruff check` / `uv run pytest` unaffected (frontend-only change).
- **Risk:** low — fallback only activates where the current code throws.
