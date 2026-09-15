## 1. BFF: bound the non-streaming agent proxy

- [x] 1.1 Split the httpx timeout policy in
      `src/frontend/webapp/server/routes_agent.py` so the client factory takes
      the read-timeout choice as a parameter: a finite read timeout for
      `_proxy_json` routes (session list, detail, create, delete, cancel,
      memory-forget), and `read=None` retained for the `_relay_sse` message
      route with bounded connect/write/pool. Verify with
      `uv run ruff check && uv run pytest tests/frontend/test_bff_agent.py`.

- [x] 1.2 Make the non-streaming read timeout configurable via an env var with
      a conservative default, documented in `.env.example`. Verify the default
      is applied when unset and the override is honoured when set.

- [x] 1.3 Map an upstream read timeout to `504` and keep connection failure at
      `503` in `_proxy_json`, distinguishing the two in the returned detail.
      Verify with a BFF contract test that patches the pi-agent transport to
      raise `httpx.ReadTimeout` and asserts the status code — add it to
      `tests/frontend/test_bff_agent.py` and confirm it fails before the
      mapping change.

- [x] 1.4 Add a BFF contract test asserting the streaming route is *not* bounded
      by the non-streaming read timeout: relay a response that pauses longer
      than the configured JSON timeout and assert events still arrive. Verify
      the test fails if the SSE client is given the finite read timeout.

## 2. SPA: replace the global session-loading gate

- [x] 2.1 Add a pure module (alongside
      `src/frontend/webapp/client/src/pages/session-transcript.ts`) mapping a
      session-operation outcome to presentation state — loading / ready / error
      with a retryable flag and a message distinguishing timeout from a
      server-reported error. Verify with a colocated
      `__tests__/*.test.ts` under the existing `environment: 'node'` vitest
      config, covering success, server error, and timeout outcomes.

- [x] 2.2 Add a client-side deadline to the session requests in
      `src/frontend/webapp/client/src/api/client.ts` (`listAgentSessions`,
      `getAgentSession`, `createAgentSession`, `deleteAgentSession`,
      `cancelAgentSession`), aborting via `AbortSignal.timeout` or an
      `AbortController`. Verify the deadline exceeds the BFF read timeout from
      1.2, and extend `src/frontend/webapp/client/src/api/__tests__/client.test.ts`
      to assert an aborted request surfaces as a timeout error.

- [x] 2.3 Replace the single `sessionLoading` boolean in
      `src/frontend/webapp/client/src/pages/AskPage.tsx` with the discriminated
      operation state from 2.1, so the composer gate depends only on a
      submission being in flight: the textarea is disabled only while sending,
      and the send button only when there is no submittable text or a send is
      in progress. Verify by re-running the reproduction in 4.2 — with the
      session route held open, both controls stay usable.

- [x] 2.4 Surface the failure state with a retry affordance for each session
      operation, and define what the thread shows after a failed restore or
      switch (cleared, or marked stale) rather than leaving a half-applied
      transcript. Verify the retry path recovers once the dependency responds,
      and that the error is cleared on success.

- [x] 2.5 Make the send path resolve its target session deterministically so a
      send can no longer race an in-flight restore into creating a duplicate
      session. Verify with a test covering send-during-restore and
      send-after-failed-restore, asserting exactly one session is created.

## 3. SPA shell: report a failed boot

- [x] 3.1 Add the pre-bootstrap guard to the Vite build (via
      `transformIndexHtml` in `src/frontend/webapp/client/vite.config.ts` or
      the source `index.html`) that registers capture-phase resource-error and
      bubble-phase error handlers plus a load deadline before the entry bundle
      is requested, revealing a static reload-prompt node on failure. Verify it
      is present in the built output: `npm run build` then inspect
      `client/dist/index.html`.

- [x] 3.2 Change the catch-all in `src/frontend/webapp/server/app.py` so a
      request for a missing path under `/assets/` is not answered with the SPA
      shell or a bare JSON 404, while leaving `/api/*`, `/health`, and `/mcp`
      unmasked. Verify with a request to a nonexistent hashed asset and confirm
      the response is not the HTML shell; confirm `/api/*` and `/mcp` behaviour
      is unchanged via `uv run pytest tests/frontend/`.

## 4. Verification

- [x] 4.1 Run the full unit gate: `uv run ruff check && uv run pytest` and
      `cd src/frontend/webapp/client && npm test`. Confirm all pass with the
      new tests included.

- [ ] 4.2 Reproduce the original defect against the live LAN deployment with
      the headless harness (Chromium at
      `/var/tmp/pw-browsers/chromium-1243/chrome-linux64/chrome`, driven by
      `playwright-core`) by holding the session route open, and confirm the
      composer stays usable and the error is surfaced and retryable. Also
      confirm the normal path still sends and streams, and that the stop
      control still interrupts a long answer.

- [ ] 4.3 Verify the deployed image, not just dev, once the pipeline has
      redeployed: confirm the built shell carries the boot guard and that a
      stale-hash asset request surfaces the recovery message rather than a
      blank page.

- [ ] 4.4 Record the outcome in `docs/todos.md` — mark the send-button item
      resolved with its root cause, and note the still-unresolved sidecar stall
      trigger as separate follow-up work.
