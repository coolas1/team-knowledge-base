# Local CI/CD pipeline

Automates the path from team-accepted code on `origin/main` to the running
LAN deployment: a systemd **user** timer polls every 5 minutes, and on a new
commit runs watch → sync → gate → build → deploy → verify on the LAN host
(the same machine as the dev checkout, rootless podman).

```
~/.config/systemd/user/team-kb-cicd.timer      (5-min cadence)
  └─ team-kb-cicd.service → <repo>/.deploy/run.sh          (static bootstrap)
                              └─ <repo>/.deploy/repo/cicd/pipeline.sh
```

`<repo>` is the dev checkout: `/home/zhangxiang/workspaces/projects/team-knowledge-base`.
The stable dir `.deploy/` is **gitignored** — it never shows in git status or
commits. Both `run.sh` and `pipeline.sh` derive the stable dir from their own
location (`TKB_CICD_HOME` overrides it for sandbox testing), so the whole
`.deploy/` tree moves with the repo; only the unit's `ExecStart` is absolute.

## Layout

| Path | Role |
|------|------|
| `<repo>/.deploy/deploy.env` | Deployment credentials + proxy settings + npm cache dir. Compose `--env-file` input; also sourced by the pipeline for proxy/`npm_config_cache`. **Lives outside the clone — wiping the clone never touches credentials.** |
| `<repo>/.deploy/run.sh` | Static bootstrap (from `cicd/run.sh.template`); initial clone + exec pipeline. Derives the stable dir from its own location. |
| `<repo>/.deploy/repo/` | Disposable clone; every run is `git fetch` + `reset --hard origin/main`. |
| `<repo>/.deploy/last-deployed` | SHA of the last commit the pipeline deployed successfully. Unchanged head ⇒ run is a no-op. |
| `<repo>/.deploy/deployed-shas` | History: `<timestamp> <sha>` per deploy (and rollbacks). |
| `<repo>/.deploy/pipeline.exec.sh` | Snapshot of the running pipeline (self-update guard, below). |
| `/var/tmp/tkb-venvs/cicd` | Gate venv (`UV_PROJECT_ENVIRONMENT`) — kept on local disk (the home filesystem is slow cephfs). |
| `/var/tmp/tkb-npm-cache` | SPA npm cache (`npm_config_cache` in deploy.env) — local disk, for the same reason. |
| `/var/tmp/node22/bin` | Node 22 for the SPA tests (system Node is 18). |

Perf note: only the clone (git objects, source tree, `node_modules`) lives on
cephfs. The gate venv, npm cache, and podman storage stay on local disk, so
the 5-min cadence is not cephfs-bound; a fresh clone's first `npm install`
is slower (~5 min observed), later runs reuse `node_modules` (`clean -fd`
keeps ignored files).

**Caveat:** because `.deploy/` lives inside the dev checkout, wiping the
checkout (e.g. `git clean -fdx` from the parent, or deleting the directory)
also wipes `deploy.env` and the deploy state. Volumes, images, and the
systemd units survive; restore `deploy.env` from the source `.env` plus the
proxy/`npm_config_cache` lines (install step 1 below) and re-run step 2.

## Self-update

`pipeline.sh` lives inside the clone it syncs, so a run always *starts* from
the previous head's copy of the script. Two guards make that safe: the
script snapshots itself to the stable dir before doing anything (`git reset
--hard` must never rewrite the file bash is executing), and after sync it
compares itself against the freshly checked-out `cicd/pipeline.sh` and
re-execs the new version when they differ — so a change to the pipeline
lands on the very run that pulls it. `rollback.sh` snapshots itself the same
way (it resets the clone to the target SHA mid-run).

## Install (one-time, on the LAN host)

```bash
# 1. Stable dir + credentials (inside the dev checkout)
mkdir -p .deploy
cp .env .deploy/deploy.env                      # from the env the live stack uses
chmod 600 .deploy/deploy.env
# Append proxy vars so git/uv/npm/podman reach the outside:
env | grep -iE '^(https?_proxy|no_proxy|npm_config_proxy|npm_config_https_proxy)=' \
  >> .deploy/deploy.env
# Keep the SPA npm cache on local disk (the clone lives on the slower home fs):
echo 'npm_config_cache=/var/tmp/tkb-npm-cache' >> .deploy/deploy.env

# 2. Bootstrap (after this change is merged to main)
install -m 755 cicd/run.sh.template .deploy/run.sh

# 3. systemd user units + linger (timers survive logout)
install -m 644 cicd/team-kb-cicd.service cicd/team-kb-cicd.timer \
  ~/.config/systemd/user/
systemctl --user daemon-reload
loginctl enable-linger "$USER"                   # verify: loginctl show-user "$USER" | grep Linger

# 4. Dry run against current origin/main (deploy withheld)
.deploy/run.sh --dry-run                         # or: systemctl --user start team-kb-cicd.service

# 5. Cutover deploy (see runbook below), then enable the timer
systemctl --user enable --now team-kb-cicd.timer  # verify: systemctl --user list-timers
```

The unit's `ExecStart` is the absolute `<repo>/.deploy/run.sh` path; if the
checkout moves, edit `ExecStart` (and `Documentation=`) in the unit and
`systemctl --user daemon-reload`.

## Cutover runbook (hand-run stack → pipeline-managed)

The stack was previously started by hand from the dev checkout; the pipeline
adopts it in one deliberate deploy:

1. Confirm nothing on the LAN used pi-agent directly at `host:8010`
   (host networking exposed it; compose publishes only `127.0.0.1:8010`).
   If something did, publish the port explicitly in `docker-compose.yml`.
2. Remove the hand-run `team-kb-pi-agent-host` container first — it was
   started outside the compose project, so `--remove-orphans` does NOT
   catch it, and it holds `:8010` (the compose pi-agent publishes
   `127.0.0.1:8010` and cannot bind while the old one runs):
   `podman rm -f team-kb-pi-agent-host`
3. Run the pipeline once with deploy enabled (or `systemctl --user start
   team-kb-cicd.service` after merging the pending work, so `origin/main`
   is at or ahead of the running stack):
   `podman compose up -d --remove-orphans` recreates the containers under
   the current compose wiring and carries the named volumes (`pgdata`,
   `neo4jdata`, `piagentdata`, `artifactsdata`); leftover in-project
   containers (e.g. an aborted `team-kb-backend`) are removed as orphans.
4. Verify: `curl http://127.0.0.1:8000/health` is green, a document ingested
   before the cutover is still searchable, and a pi-agent MCP round-trip
   succeeds (proves the in-network `webapp:8000` host trust).
5. Enable the timer (install step 5 above). From here on, merges to `origin/main`
   deploy automatically within ~5 minutes.

## Rollback

```bash
# List deployed SHAs (newest last)
cat .deploy/deployed-shas
.deploy/repo/cicd/rollback.sh <sha>
```

Rollback retags the SHA-tagged images as `:latest`, checks the clone out at
that SHA (so the compose file matches), and redeploys. It deliberately does
not rewrite `last-deployed`: while `origin/main` stays put, the timer no-ops
and the rollback sticks; the next merge deploys forward again. To pin a
rollback longer, `systemctl --user stop team-kb-cicd.timer`.

## Ownership rule

**The pipeline is the sole operator of the `team-kb` compose project.** Never
run `podman compose up`/`down` from the dev checkout or by hand: compose fixes
`name: team-kb` and the container names, so a second operator collides with
the pipeline. The dev checkout is a *client* of the LAN stack — reach
Postgres/Neo4j/webapp only through the published ports (5433 / 7687 / 8000).

## Failure semantics

- Gate or build failure ⇒ non-zero exit before deploy; the running stack is
  untouched and keeps serving; the failure is visible in
  `systemctl --user status team-kb-cicd` and
  `journalctl --user -u team-kb-cicd.service`.
- Unhealthy after deploy ⇒ non-zero exit (the new version stays up; run the
  rollback command if needed).
- Every successful build is tagged `:<short-sha>` alongside `:latest`, so
  rollback needs no rebuild.

`RUN_INTEGRATION=1` tests are deliberately **not** in the gate — they point at
the production backing services.
