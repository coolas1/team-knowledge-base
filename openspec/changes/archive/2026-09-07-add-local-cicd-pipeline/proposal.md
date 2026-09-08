## Why

The LAN knowledge-base service is deployed by hand — `podman compose up -d`
run from the dev checkout — so it silently drifts from accepted work: the
currently running stack predates the embedding-config compose wiring and its
pi-agent path is broken (the sidecar was hand-run with `--network host` to
work around an MCP host-allowlist miss, and the webapp's `PI_AGENT_URL` no
longer resolves). Team-accepted code on `origin/main` (the PR review line)
has no automated path to deployment.

## What Changes

- Add a local CI/CD pipeline on the LAN host: a systemd user timer polls
  `origin/main` (~5 min); on a new SHA it syncs a disposable clone under
  `/var/tmp/team-kb-cicd`, gates on lint + unit tests, builds SHA-tagged
  images, redeploys the stack with `podman compose`, and verifies `/health`.
- Deploy credentials live in a stable `deploy.env` outside the disposable
  clone, passed via compose `--env-file`; wiping the clone never touches
  credentials.
- Fix the MCP DNS-rebind allowlist (`src/agent/tkb/mcp/server.py`) to accept
  the current compose service name so pi-agent runs as a normal compose
  service — retiring the hand-run `--network host` workaround.
- Track the pipeline script and systemd unit files in the repo under `cicd/`
  (versioned with the code it deploys); a static bootstrap wrapper in the
  stable dir invokes it.
- One-time cutover: the pipeline adopts the hand-run stack — containers are
  recreated from the current compose file, named data volumes carry over,
  `--remove-orphans` retires `team-kb-pi-agent-host`.
- Failure safety: failed tests or build leave the currently running service
  untouched; rollback redeploys a prior SHA-tagged image (manual in v1).
- The pipeline exclusively owns the `team-kb` compose project; the dev
  checkout interacts with the LAN stack only through published ports
  (5433/7687/8000), never via `compose up`.

## Capabilities

### New Capabilities
- `local-cicd`: watch `origin/main`, gate on tests, build and deploy the LAN
  service via rootless podman compose; failure and rollback semantics; data
  persistence across deploys; single-operator ownership of the stack.
- `mcp-host-trust`: which Host headers the `/mcp` endpoint accepts —
  localhost plus deployment-internal service names — so in-network clients
  (pi-agent sidecar) authenticate without host-network workarounds.

### Modified Capabilities

(none — `openspec/specs/` is currently empty)

## Impact

- **Code**: one-line allowlist addition in `src/agent/tkb/mcp/server.py`; new
  `cicd/` directory (pipeline script, systemd unit templates, install notes).
  No dependency changes.
- **Systems**: rootless podman (compose via docker-compose v2 plugin, storage
  already on `/var/tmp`); systemd user units; new stable dir
  `/var/tmp/team-kb-cicd` (deploy.env + bootstrap + disposable clone).
- **Ops/workflow**: LAN service becomes pipeline-managed after a one-time
  cutover; pi-agent moves from host networking to the compose bridge, so it
  is reachable only on `127.0.0.1:8010` (today, host networking exposes it
  on the LAN at `:8010` — assumed unused directly; to confirm at cutover).
- **Tests in the gate**: `ruff check`, `uv run pytest` (unit/contract/BFF),
  SPA `npm test` under Node 22 from `/var/tmp/node22` (system Node 18 fails
  3 upload tests environmentally). `RUN_INTEGRATION=1` stays out of the gate.
- **First deploy**: idles until a PR advances `origin/main` (currently behind
  local main, `origin/develop`, and the running stack); that deploy doubles
  as the migration.
