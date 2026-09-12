## 1. Turn deadline

- [x] 1.1 Add `PI_AGENT_MAX_RUN_SECONDS=300` to the LAN deployment's `.deploy/deploy.env` (the compose `--env-file`); the tracked `docker-compose.yml` keeps its universal `${PI_AGENT_MAX_RUN_SECONDS:-180}` default. Record the setting in `cicd/README.md` and the source `.env` (deploy.env is rebuilt from it on reinstall). Verify the hierarchy check passes at startup (deep 60s + reserve 60s < 300s) by running the sidecar's config validation or booting it locally with the same env.
- [x] 1.2 Confirm the SPA stream path is unaffected: the 45s client deadline applies only to non-streaming session ops and the BFF exempts streaming routes — note the evidence (code refs) in the PR description.

## 2. Failure diagnostics

- [x] 2.1 In `src/extensions/pi-agent/src/runtime.ts`'s turn-failure catch, log the redacted underlying error with session and turn ids via `console.error`, beside the existing `logTranscript` call. Add/extend the sidecar unit test asserting the log line on a forced turn failure.

## 3. Verification

- [x] 3.1 Sidecar suite: `cd src/extensions/pi-agent && npm test` (or the repo's equivalent for the sidecar) passes; `uv run ruff check` and `uv run pytest` from root stay green (guards against accidental Python edits).
- [ ] 3.2 Post-release (LAN): confirm `PI_AGENT_MAX_RUN_SECONDS=300` in the deployed sidecar and that a failed turn's cause appears in `podman logs team-kb-pi-agent`; record evidence under `bench/`.
