## Context

See proposal.md for motivation. Facts that shape this design:

- The LAN host is also the dev box. The live stack runs under **rootless
  podman** (docker socket denied for this user); compose runs via
  `podman compose`, which delegates to the docker-compose v2 plugin. Podman
  storage already lives on local disk (`/var/tmp/user-cache/1001/podman`).
- `/home` is cephfs — slow for package trees and build contexts. `/tmp`,
  `/var/tmp`, `/opt` are local ext filesystems.
- System Node is 18; SPA client tests need Node 22 (3 upload tests fail
  under 18 for environmental reasons). A persistent Node 22 lives at
  `/var/tmp/node22/bin`. The dev checkout already symlinks its venv to
  `/var/tmp/tkb-venvs/tkb` for the same cephfs reason.
- The live stack was hand-started from the dev checkout (compose project
  `team-kb`); pi-agent was hand-run with `--network host` as
  `team-kb-pi-agent-host` because the MCP DNS-rebind allowlist
  (`src/agent/tkb/mcp/server.py:32`) never included the current service
  name `webapp:8000` (it has stale `backend:8000` and the container name).
- The BFF exposes `GET /health` (`src/frontend/webapp/server/app.py:71`).
- Branch flow: work goes to `origin/develop`, is reviewed, and merges to
  `origin/main` — the team's accepted line. Push works from this machine.
- `RUN_INTEGRATION=1` tests have never run live; they need Postgres+Neo4j
  and would point at the production volumes.

## Goals / Non-Goals

**Goals:**

- `origin/main` head → running LAN service, hands-off, within ~minutes.
- A failed gate or build never touches the running service.
- Deployed data (volumes) survives every deployment.
- Exactly one operator of the `team-kb` compose project.
- Pipeline logic versioned in the repo it deploys.

**Non-Goals:**

- GitHub-hosted CI, PR status reporting, release branches/tags.
- Integration tests in the gate (needs throwaway backing services; costly).
- Zero-downtime/blue-green deploys (restart-bounded downtime is fine).
- Automatic rollback (v1 keeps prior images; rollback is a manual command).

## Decisions

1. **Trigger: systemd user timer polling `git ls-remote`** every 5 min.
   `ls-remote` is one HTTPS roundtrip through the working proxy; a GitHub
   webhook would need a publicly reachable endpoint (NAT'd LAN host — no);
   a long-running poller daemon adds state for nothing. systemd already
   prevents overlapping runs of the same unit, which is the locking story.

2. **Runtime: rootless podman + `podman compose`** — matches the live stack,
   the relocated storage, and the docker-compose-v2 provider already proven
   here. Alternatives: the docker daemon (socket denied), host-run uvicorn
   (loses containerized tesseract/Node, no clean lifecycle).

3. **Stable dir + disposable clone.** `/var/tmp/team-kb-cicd/` holds
   `deploy.env` (credentials), `run.sh` (static bootstrap), and `repo/`
   (disposable clone; each run is `git fetch` + `reset --hard origin/main`).
   Credentials and the bootstrap survive `rm -rf repo/`. Everything heavy
   (clone, venv, npm cache, podman storage) stays on ext, not cephfs.
   Alternative rejected: cloning into the compose project of the dev
   checkout (cephfs; and couples dev state to deploys).

4. **Pipeline code tracked in `cicd/` in the repo.** The systemd unit's
   `ExecStart` calls the stable bootstrap `run.sh`, which execs
   `repo/cicd/pipeline.sh`. This resolves the chicken-and-egg (units need a
   stable path; the script should evolve with the code it deploys) and the
   bootstrap is intentionally trivial (~5 lines, rarely changes).

5. **Gate = `ruff check` + `uv run pytest` + SPA `npm test`.** Tests run on
   the host against the clone, with `UV_PROJECT_ENVIRONMENT=/var/tmp/tkb-venvs/cicd`
   (local disk, per-clone venv) and `PATH=/var/tmp/node22/bin:$PATH` for the
   SPA suite. The warm npm cache is reused (do not override
   `npm_config_cache`). Integration tests stay out — never run live, would
   touch production volumes.

6. **Images tagged `:<short-sha>` and `:latest`** by `podman compose build`;
   deploy is `podman compose up -d --remove-orphans`. Rollback = point
   compose at a prior `:<sha>` image (manual command documented in
   `cicd/`). Alternative rejected: a local image registry (nothing else
   consumes these images).

7. **MCP allowlist: add `webapp:8000`** alongside the existing entries
   (keep stale `backend:8000` for compat). Alternatives rejected: disabling
   DNS-rebind protection (weakens security for nothing), an env-driven
   allowlist (YAGNI for one deployment), a compose override keeping pi-agent
   on host networking (perpetuates the special case and the currently-broken
   `PI_AGENT_URL` resolution).

8. **Ownership rule: the pipeline is the sole compose operator.** Compose
   fixes `name: team-kb` and container names, so a second operator would
   collide. The dev checkout stays a client over published ports
   (5433/7687/8000). Enforcement is by convention + docs in v1.

9. **Verification: `curl /health`** with a bounded timeout after `up -d`;
   failure exits non-zero (visible in the journal + systemd unit state).

## Risks / Trade-offs

- [Something on the LAN uses pi-agent directly at `host:8010` today (host
  networking exposes it); compose binds it to `127.0.0.1:8010`] → confirm at
  cutover; if used, publish the port deliberately in compose.
- [systemd user timers stop when the user has no session] → `loginctl
  enable-linger` for the deploying user, verified during install.
- [/var/tmp is subject to systemd tmpfiles age-based cleaning (~30d unused)]
  → the timer touches the dir every 5 min; risk is theoretical. Relocate to
  `/opt` with sudo if it ever bites.
- [First pipeline deploy serves `origin/main`, which is currently *behind*
  local main (+26), `origin/develop` (+8), and the hand-run stack] →
  accepted: the hand-run stack is drifted and partially broken anyway;
  merge the pending work and the pipeline catches up on the next poll.
- [Podman builds are slow through the proxy] → mitigated by the
  Containerfile's layer ordering, digest-pinned uv, and the aliyun mirror;
  builds only run on new SHAs.
- [Concurrent manual `podman compose up` from the dev checkout fights the
  pipeline] → ownership rule (Decision 8); both actors would converge on the
  same fixed container names, so worst case is churn, not data loss.
- [Cutover recreates containers → brief downtime + first-run surprises] →
  scheduled cutover with a runbook; named volumes persist; old images remain
  for instant rollback.

## Migration Plan

1. Land the allowlist fix and `cicd/` files through the normal PR flow
   (develop → review → main).
2. Create `/var/tmp/team-kb-cicd/`; seed `deploy.env` from the `.env` the
   live stack currently runs with; install `run.sh`; install the systemd
   user units; `loginctl enable-linger`.
3. Manual dry-run of the pipeline stages (sync → gate → build) against
   current `origin/main`, deploying deliberately withheld.
4. Cutover deploy: `podman compose up -d --remove-orphans --env-file
   deploy.env` from `repo/` — adopts project `team-kb`, recreates containers
   under current compose wiring, carries the named volumes, removes
   `team-kb-pi-agent-host` as an orphan.
5. Verify: `/health`, a search, an agent round-trip. Enable the timer.
6. Rollback path at any step: prior SHA image, or stop the units and revert
   to the previous containers/images (still present locally).

## Open Questions

- Exact poll cadence (5 min proposed) — tune after observing merge-to-deploy
  latency; a timer edit, no code change.
- Whether anything on the LAN calls pi-agent directly (affects only whether
  we publish its port in compose) — answered at cutover.
- Auto-rollback on failed health check, and an env-driven MCP allowlist —
  both safe v2 additions.
