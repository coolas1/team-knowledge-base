## Why

v0.3.1 shipped `enable-agent-tool-authoring` with tool authoring defaulting
ON, but the runner was never deployable on the LAN stack: the deployment runs
rootless podman, while the gateway assumed a Docker daemon socket. Every
`execute_code` failed with `runner_unavailable: configure runner URL and
authentication token`, and authoring had to be disabled on the LAN as a
stopgap (the README's documented rollback path). The agent's code-execution
capability is therefore shipped but inert in production.

## What Changes

- **Podman-compatible job template** (`src/extensions/tool-runner/src/jobs.ts`):
  the `/work` tmpfs options drop `uid=1000,gid=1000` (rejected by podman 4.9's
  Docker-compat API as "invalid mount option") and use `mode=0777` instead.
  Safe: each job mounts a private per-container tmpfs in an isolated
  single-uid container.
- **Prompt job cleanup** (`src/extensions/tool-runner/src/jobs.ts`): cleanup
  SIGKILLs the container before `DELETE ?force=1`. Podman's compat
  force-delete waits out its ~10 s stop timeout on wedged jobs (busy loops,
  process floods), exceeding the gateway's 10 s request timeout; Docker kills
  immediately, so the explicit SIGKILL is a no-op there.
- **Parameterized gateway socket** (`docker-compose.yml`): the tool-runner
  volume becomes
  `${TOOL_RUNNER_DOCKER_SOCKET:-/var/run/docker.sock}:/var/run/docker.sock`,
  so podman deployments can mount `/run/user/<uid>/podman/podman.sock`.
  Docker deployments are unchanged by default.
- **Pipeline builds and deploys the opt-in tool services**
  (`cicd/pipeline.sh`): the build stage also builds the profile-gated tool
  images (`tool-job` behind `tool-images`, `tool-runner` behind
  `tool-authoring`) and SHA-tags them alongside the other images; the deploy
  stage honors `COMPOSE_PROFILES` from `deploy.env` (verified: podman compose
  `--env-file` activates profile services), so `up -d --remove-orphans`
  starts tool-runner instead of skipping or orphan-removing it.
- **Docs and env template**: podman deployment section in the tool-runner
  README (socket override, `COMPOSE_PROFILES=tool-authoring`,
  `systemctl --user enable podman.socket`, generated token ≥ 24 chars,
  `PI_AGENT_RUNNER_URL=http://tool-runner:8020`); new vars in
  `.env.example`.
- **Tests**: unit coverage for the podman-compatible tmpfs options and the
  kill-before-delete ordering.

Sandbox-verified end-to-end (2026-09-13): the full 12-test tool-runner
integration suite passes against `/run/user/1001/podman/podman.sock`, and a
containerized gateway with the socket mounted executed a real code job over
its HTTP API.

Deployment-side (untracked, applied at release time, not part of this
change's code): `.deploy/deploy.env` gains `PI_AGENT_RUNNER_URL`,
`PI_AGENT_RUNNER_TOKEN`, `TOOL_RUNNER_DOCKER_SOCKET`,
`COMPOSE_PROFILES=tool-authoring` and drops the stopgap
`PI_AGENT_TOOL_AUTHORING_ENABLED=false`; `systemctl --user enable
podman.socket` for boot resilience.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `agent-tool-authoring`: the gateway must operate against any
  Docker-compatible container API (Docker or rootless podman) — job templates
  use only options both runtimes accept, and job cleanup terminates wedged
  containers promptly on both.
- `local-cicd`: the pipeline's build stage must build the images of services
  the deployment has enabled via compose profiles (the tool-authoring pair)
  and SHA-tag them, and the deploy stage must start and retain those
  opt-in services.

## Impact

- `src/extensions/tool-runner/src/jobs.ts` (job template tmpfs options;
  SIGKILL-before-delete cleanup for both job teardown and stale-job sweep at
  startup).
- `src/extensions/tool-runner/tests/` (new unit tests; integration suite
  unchanged and passing).
- `docker-compose.yml` (tool-runner volume parameterization only).
- `cicd/pipeline.sh` (build profiles, `compose_images` tagging list).
- `src/extensions/tool-runner/README.md`, `.env.example` (documentation).
- No API, schema, or protocol changes; no effect on deployments that do not
  enable the `tool-authoring` profile. The LAN deployment additionally needs
  the untracked `deploy.env` entries above before the runner activates.
