## Context

See proposal.md — Why. The single `crypto.*` use in the SPA is the
`clientMessageId` generated in the Ask page send path
(`src/frontend/webapp/client/src/pages/AskPage.tsx:299`). The deployment serves
the SPA over plain HTTP on a LAN IP; browsers expose `crypto.randomUUID` only in
secure contexts (HTTPS or localhost), so on that origin the send path throws
after clearing the composer and before any request fires. The client currently
has no `utils/` module; shared client helpers live in `src/api/` beside
`client.ts`, with tests colocated in `src/api/__tests__/`.

## Goals / Non-Goals

**Goals:**

- Sending a question works identically from secure and insecure origins.
- Generated `clientMessageId` values remain UUID-v4-format strings, so the
  BFF/agent contract (idempotency key, transcript dedupe) is unchanged.

**Non-Goals:**

- Making the deployment a secure context (TLS/HTTPS for the LAN origin) — the
  durable fix for the whole class of bugs, but it needs cert infrastructure and
  is a deployment decision, not a client change.
- Auditing/hardening every page for other secure-context-only APIs beyond the
  send path (grep shows no other `crypto.*` use today).
- Error-surfacing improvements for pre-`try` failures in `run()` and the
  ask-latency dead zone (separately tracked in the QA report).

## Decisions

- **Fallback via `crypto.getRandomValues`, RFC 4122 v4.** New helper
  `randomUUID()` in `src/api/uuid.ts`: use `crypto.randomUUID()` when it is a
  function; otherwise build a v4 UUID from 16 `getRandomValues` bytes, setting
  the version (byte 6) and variant (byte 8) bits. `getRandomValues` is not
  secure-context-gated, so this covers every browser the SPA supports. If
  neither API exists, throw a descriptive error — fail loudly rather than
  silently dropping another message.
  - *Math.random-based ids*: rejected — weaker uniqueness for no benefit, since
    `getRandomValues` is available everywhere `randomUUID` is missing.
  - *uuid npm package*: rejected — adds a dependency for ~6 lines with no
    feature we need.
- **Placement in `src/api/` (not a new `src/utils/`).** The helper's only
  consumer is the ask flow's API contract; colocating with `client.ts` follows
  the existing structure. A `utils/` dir can appear later if unrelated helpers
  accumulate.
- **One-line call-site swap** in `AskPage.tsx` (`crypto.randomUUID()` → the
  helper). No signature or behavioral change elsewhere; `streamAgentMessage`
  already treats `clientMessageId` as an opaque string.
- **Tests colocated in `src/api/__tests__/uuid.test.ts`** (vitest): secure path
  passes through to `crypto.randomUUID`; insecure path (randomUUID deleted from
  the global in the test) yields a valid v4 UUID (regex + version/variant bits)
  and distinct values across calls.

## Risks / Trade-offs

- [Future client code reintroduces a secure-context-only API] → no automated
  guard exists today; mitigated by convention + the QA practice of testing via
  the LAN origin (recorded in `bench/qa-ask-latency-2026-09-11/REPORT.md`).
  A lint rule or CI browser-context check can be added later if the pattern
  recurs.
- [Test environments where the global `crypto` is partial] → tests stub/restore
  the global explicitly rather than assuming jsdom's capabilities.

## Migration Plan

Frontend-only change; ships through the normal feature-branch → PR to
`develop` → maintainer release to `main` flow. Rollback is a revert of the
commit; no data or wire-format migration involved.
