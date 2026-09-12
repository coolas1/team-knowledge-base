## Why

The sidecar's turn deadline (`PI_AGENT_MAX_RUN_SECONDS`) runs on the 180s
code default — never set for this deployment — while the LLM backend's
lifetime mean is **128 s/request** (319 requests, 40,917 s). On 2026-09-11
(session `01a090b6`) a healthy-shaped turn died at 176s (`agent_failed`) and
the retry produced **0 tokens for the full 180s** before `time_limit` cut it
off. On this backend a multi-iteration turn cannot reliably fit 180s even
when nothing is broken. The same incident showed `agent_failed` logs nothing
about the cause — the underlying error (a degenerate 1-token model response)
was only recoverable by digging through session files on the container.

## What Changes

- Set `PI_AGENT_MAX_RUN_SECONDS=300` in the LAN deployment's machine-local
  `.deploy/deploy.env` (the pipeline's compose `--env-file`), not in the
  tracked compose file — the slow LLM backend is a property of this machine,
  so the universal compose default stays 180. The existing timeout-hierarchy
  validation stays satisfied (60s deep-tool timeout + 60s reserve = 120 < 300).
- The sidecar logs the underlying error (redacted) when a turn fails, so
  `agent_failed` is diagnosable from `podman logs` instead of session-file
  archaeology.

Non-goals: LLM backend capacity (infra; QA report recommendations stand);
context growth (separate change `bound-agent-tool-results`); any UI change —
the Ask page already renders a failure status.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `app-deployment`: adds requirements that the deployment configures the
  agent turn deadline above the LLM backend's realistic worst-case turn, and
  that turn failures log their underlying cause.

## Impact

- **Code:** `.deploy/deploy.env` (machine-local, untracked — adds
  `PI_AGENT_MAX_RUN_SECONDS=300`; `docker-compose.yml`'s
  `${PI_AGENT_MAX_RUN_SECONDS:-180}` substitution stays untouched), a note in
  `cicd/README.md` so re-installs keep the setting, and
  `src/extensions/pi-agent/src/runtime.ts` (one error-log line in the turn
  failure path, using the existing `redact()` helper).
- **Compatibility:** turns may now run up to 5 minutes before failing —
  the Ask UI's loading state and the client deadline
  (`AGENT_REQUEST_TIMEOUT_MS`) must still bound the request; verify the
  client deadline exceeds 300s or streams unbounded (streaming route is
  exempt from the BFF read timeout by design).
- **Validation:** sidecar test suite; post-deploy turn-failure observability
  check.
- **Risk:** low — one env var and one log line.
