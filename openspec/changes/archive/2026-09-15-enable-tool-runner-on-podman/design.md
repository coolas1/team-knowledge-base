## Context

The LAN deployment runs the stack under rootless podman (uid 1001, systemd
user units, `podman compose` with the docker-compose provider over the podman
socket). The tool-runner gateway shipped in v0.3.1 assumes a Docker daemon:
it mounts `/var/run/docker.sock` (owned root:docker on this host, unreadable
by the deployment user), and its job template and cleanup use Docker-only
behaviors. The pipeline builds only the default-profile services, and the
`tool-job`/`tool-runner` images sit behind the `tool-images`/`tool-authoring`
compose profiles that no stage activates. Podman here is 4.9.3 with its
Docker-compatible REST socket at `/run/user/1001/podman/podman.sock`.

Sandbox findings (2026-09-13, against the live podman socket):
- Compat `POST /containers/create` rejects the job template's tmpfs options
  `uid=1000,gid=1000` ("invalid mount option"); Docker accepts them. Options
  without `uid=`/`gid=` are accepted, and `mode=` works on both.
- Compat `DELETE ?force=1` on a running container stops it with podman's
  default ~10 s SIGTERM-wait before SIGKILL; wedged jobs (busy loops,
  40-process floods) therefore exceed the gateway's 10 s request timeout.
  An explicit `POST /containers/{id}/kill?signal=SIGKILL` first is immediate
  on both runtimes.
- `COMPOSE_PROFILES=tool-authoring` inside the `--env-file` activates the
  profile for `podman compose config/build/up` (verified with `config
  --services`).
- With both fixes, the full 12-test integration suite passes against the
  podman socket, and a containerized gateway with the socket mounted served
  `/health` `available:true` and executed a real job over its HTTP API.

## Goals / Non-Goals

**Goals:**
- One code path that works on Docker and rootless podman alike — no runtime
  detection in the gateway.
- Keep the runner opt-in per deployment (compose profile), with zero effect
  on deployments that do not enable it.
- The pipeline builds and SHA-tags the tool images so rollback can pin them
  like the other images.

**Non-Goals:**
- Upgrading podman or changing the LAN's container runtime.
- Supporting arbitrary container runtimes beyond Docker-compatible sockets.
- Capability profiles (`RUNNER_PROFILES_FILE`) for authorized APIs —
  unchanged, still administrator opt-in.
- Fixing rootless-podman reboot behavior for the stack generally (the
  `podman.socket` enable step only keeps the gateway's socket alive).

## Decisions

**Single option set instead of runtime detection.** The tmpfs options become
`rw,noexec,nosuid,nodev,size=32m,mode=0777` on both runtimes. Alternative
rejected: detect podman via `GET /version` and branch — two code paths to
test, and Docker accepts the portable set anyway. `/work` becomes
world-writable inside the job container; this is safe because each job mounts
its own private tmpfs (`noexec,nosuid,nodev`) in a single-uid isolated
container — no other process can reach it. The image's own `/work` ownership
(`chown node:node`) remains but is shadowed by the mount, as before.

**SIGKILL before DELETE, centralized in one `remove()` helper.** Both the
per-job teardown (`finally`) and the startup sweep of stale owned containers
go through the same `remove(id)`: best-effort `kill?signal=SIGKILL` (ignore
"not running"/409-style errors), then `DELETE ?force=1`. On Docker the kill
is a no-op for exited containers and immediate for running ones; on podman it
sidesteps the stop timeout. Alternative rejected: raising the client request
timeout — it would leave wedged jobs blocking cleanup for the full stop
timeout per container and only masks the difference.

**Socket path via compose variable, not gateway env.** The volume becomes
`${TOOL_RUNNER_DOCKER_SOCKET:-/var/run/docker.sock}:/var/run/docker.sock`;
the in-container path stays `/var/run/docker.sock` so the gateway code and
its `RUNNER_DOCKER_SOCKET` default are untouched. Docker deployments see no
diff. Alternative rejected: adding `RUNNER_DOCKER_SOCKET` to the compose env
block — the socket must be mounted into the container anyway, so the mount is
the only place the host path can vary.

**Build uses explicit profiles; deploy honors `COMPOSE_PROFILES` from
deploy.env.** The build stage invokes compose build with
`--profile tool-images` (so the always-needed `tool-job` image builds) and
`--profile tool-authoring` (the gateway image); the deploy stage stays
generic — `COMPOSE_PROFILES=tool-authoring` in the deployment's env file
activates the service set, which `podman compose --env-file` honors (and
docker-compose likewise). This keeps the runner opt-in per deployment and
makes `rollback.sh` (same `up -d --remove-orphans` via env file) profile-aware
without modification: rolling back to a pre-runner compose file removes the
runner as intended, rolling forward keeps it. `compose_images` gains
`team-kb-tool-job` and `team-kb-tool-runner` so SHA tags exist for rollback.
Building the `tool-images` profile never runs it: `tool-job` is build-only by
compose target, and the deploy stage's profile set excludes it.

**Gateway runs as container-root → host uid 1001.** The gateway Containerfile
sets no `USER`, so under rootless podman the mounted podman socket (owned by
host uid 1001) is accessible as container-root. No image change needed.

## Risks / Trade-offs

- [Older/newer podman may differ from 4.9.3's compat quirks] → the fixes are
  conservative subsets of Docker behavior (no uid= options; explicit SIGKILL),
  which every Docker-compatible runtime must accept; the integration suite is
  the regression net and now runs against the podman socket in the sandbox.
- [`/work` mode 0777 weakens intra-job hardening] → mitigated by per-container
  private tmpfs with `noexec,nosuid,nodev` and a single uid (1000) in the job
  container; the isolation test asserts other jobs' files and host paths stay
  unreachable.
- [Gateway owns the user's podman socket = same trust as Docker socket] →
  unchanged from the Docker design (gateway is a host administrator); the
  socket is scoped to the deployment user, not root.
- [`podman.socket` not enabled at boot] → README documents
  `systemctl --user enable podman.socket`; deployment step applies it. Without
  it the gateway reports unavailable after a reboot while the rest of the
  stack behaves as today (pre-existing rootless-podman reboot caveat, not
  introduced here).
- [Pipeline builds tool images on deployments that never enable the profile]
  → bounded cost: two small node images; harmless to build and tag. Preferred
  over conditional build logic in the pipeline.

## Migration Plan

Deploy (at release time, after this change reaches main): populate
`.deploy/deploy.env` (`PI_AGENT_RUNNER_URL=http://tool-runner:8020`,
generated `PI_AGENT_RUNNER_TOKEN` ≥ 24 chars,
`TOOL_RUNNER_DOCKER_SOCKET=/run/user/1001/podman/podman.sock`,
`COMPOSE_PROFILES=tool-authoring`; remove the stopgap
`PI_AGENT_TOOL_AUTHORING_ENABLED=false`), run
`systemctl --user enable podman.socket`, then force the pipeline. Verify:
`tool-runner` container running, pi-agent `/health` reports
`toolAuthoring.enabled=true` and `runner.available=true`, and a real
`execute_code` round-trip succeeds.

Rollback: clear `PI_AGENT_RUNNER_URL` and `PI_AGENT_TOOL_AUTHORING_ENABLED`,
remove `COMPOSE_PROFILES` from the env file, recreate the stack —
`--remove-orphans` then stops the runner; the tool library volume is
preserved per the existing README rollback section.
