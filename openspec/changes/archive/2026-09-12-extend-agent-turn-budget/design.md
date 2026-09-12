## Context

See proposal.md — Why. The sidecar reads `PI_AGENT_MAX_RUN_SECONDS` (code
default 180, `config.ts:198`) with a startup hierarchy check:
`deepToolTimeoutMs (60s) + turnReserveSeconds (60s) < maxRunSeconds`
(`config.ts:205`). The deployment's compose file sets neither, so it runs on
the default. The SPA's 45s client deadline (`AGENT_REQUEST_TIMEOUT_MS`)
bounds only non-streaming session ops (`client.ts:426-454`) — the message
stream is bounded solely by the sidecar turn budget, and the BFF exempts
streaming routes from its read timeout (per `app-deployment` spec). The turn
failure path (`runtime.ts:553-577`) emits a transcript event and SSE
`message.failed` but never logs the caught error.

## Goals / Non-Goals

**Goals:**

- Turns that are slow-but-healthy complete on the LAN LLM backend.
- Turn failures explain themselves in `podman logs`.

**Non-Goals:**

- Backend capacity, context bounding, or UI changes (see proposal).

## Decisions

- **Machine-local deploy.env, not a compose hardcode or code-default
  change.** 180s remains the sane library default and the universal compose
  default — `docker-compose.yml`'s `${PI_AGENT_MAX_RUN_SECONDS:-180}` stays
  untouched. The slow backend is a property of this machine, so the 300s
  override belongs in `.deploy/deploy.env` (the pipeline's compose
  `--env-file`, never committed). One line: `PI_AGENT_MAX_RUN_SECONDS=300`.
  Hierarchy stays valid (60+60 < 300). Because `deploy.env` is rebuilt from
  the source `.env` on reinstall, the setting is recorded in `cicd/README.md`
  and the source `.env` so a re-provision doesn't silently drop it back to 180.
- **300s, not more.** The incident's healthy-shaped turn died at 176s; with
  `bound-agent-tool-results` shrinking per-iteration prefills, 300s fits
  several bounded iterations with margin. Going higher buys little and makes
  a genuinely-wedged turn hold the UI longer before failing.
- **One log line in the failure path.** In the `catch` at `runtime.ts:553`,
  `console.error` the redacted error (`redact()` from `runner-client.ts`,
  already imported in that module family) with session/turn ids, next to the
  existing `logTranscript` call. No change to the SSE contract or the
  transcript journal.

## Risks / Trade-offs

- [Users wait up to 5 minutes on a wedged turn] → the stop control is
  available throughout; failure states surface as today. The alternative
  (180s) failed both incident turns anyway.
- [Long-running streaming connection held open] → BFF already exempts
  streaming from read timeouts; no proxy change needed.

## Migration Plan

One env line plus one log line; rollback = revert + redeploy. Verify after
release: `podman exec team-kb-pi-agent env | grep MAX_RUN` reports 300 and a
deliberately failed turn (or the next natural one) shows its cause in
`podman logs team-kb-pi-agent`.
