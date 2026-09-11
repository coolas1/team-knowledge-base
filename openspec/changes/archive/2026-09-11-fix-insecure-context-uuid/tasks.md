## 1. UUID helper

- [x] 1.1 Create `src/frontend/webapp/client/src/api/uuid.ts` exporting `randomUUID(): string` — returns `crypto.randomUUID()` when it is a function, else builds an RFC 4122 v4 UUID from `crypto.getRandomValues(new Uint8Array(16))` with version/variant bits set, and throws a descriptive error if neither API exists. Verify with `cd src/frontend/webapp/client && npx tsc --noEmit`.
- [x] 1.2 Add `src/frontend/webapp/client/src/api/__tests__/uuid.test.ts`: (a) secure path delegates to `crypto.randomUUID`; (b) insecure path (stub the global with `randomUUID` deleted) returns values matching the v4 UUID regex with correct version/variant nibbles and distinct values across calls; restore the global afterward. Verify with `npm test -- uuid`.

## 2. Call-site swap

- [x] 2.1 In `src/frontend/webapp/client/src/pages/AskPage.tsx`, replace the `crypto.randomUUID()` call (~line 299) with the helper import. Verify `grep -rn "crypto\." src/frontend/webapp/client/src --include=*.ts --include=*.tsx | grep -v test` returns no call sites outside `uuid.ts`.
- [x] 2.2 Run the full SPA suite: `cd src/frontend/webapp/client && npm test` — all tests pass, including existing ask/session tests.

## 3. Cross-context verification

- [x] 3.1 Build the SPA (`npm run build`) and serve it locally; with the `repro-insecure.js` harness pattern (headless Chrome against a non-localhost origin, e.g. `http://10.201.110.190:8000` from `bench/qa-ask-latency-2026-09-11/harness/`), confirm `window.isSecureContext === false`, no `crypto.randomUUID` error, and the sent question reaches the server and streams an answer. (Full end-to-end on the LAN deployment happens after the release deploys; before that, verify against a locally served build.)
- [x] 3.2 Repo checks from root: `uv run ruff check` and `uv run pytest` still pass (frontend-only change; guards against accidental Python edits).
