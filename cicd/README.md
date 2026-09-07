# Local CI/CD pipeline

Automates the path from team-accepted code on `origin/main` to the running
LAN deployment: a systemd **user** timer polls every 5 minutes, and on a new
commit runs watch → sync → gate → build → deploy → verify on the LAN host
(the same machine as the dev checkout, rootless podman).

```
~/.config/systemd/user/team-kb-cicd.timer      (5-min cadence)
  └─ team-kb-cicd.service → /var/tmp/team-kb-cicd/run.sh   (static bootstrap)
                              └─ /var/tmp/team-kb-cicd/repo/cicd/pipeline.sh
```

## Layout

| Path | Role |
|------|------|
| `/var/tmp/team-kb-cicd/deploy.env` | Deployment credentials + proxy settings. Compose `--env-file` input; also sourced by the pipeline for proxy. **Lives outside the clone — wiping the clone never touches credentials.** |
| `/var/tmp/team-kb-cicd/run.sh` | Static bootstrap (from `cicd/run.sh.template`); initial clone + exec pipeline. |
| `/var/tmp/team-kb-cicd/repo/` | Disposable clone; every run is `git fetch` + `reset --hard origin/main`. |
| `/var/tmp/team-kb-cicd/last-deployed` | SHA of the last commit the pipeline deployed successfully. Unchanged head ⇒ run is a no-op. |
| `/var/tmp/team-kb-cicd/deployed-shas` | History: `<timestamp> <sha>` per deploy (and rollbacks). |
| `/var/tmp/team-kb-cicd/pipeline.exec.sh` | Snapshot of the running pipeline (self-update guard, below). |
| `/var/tmp/tkb-venvs/cicd` | Gate venv (`UV_PROJECT_ENVIRONMENT`) — kept off the slow home filesystem. |
| `/var/tmp/node22/bin` | Node 22 for the SPA tests (system Node is 18). |

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
# 1. Stable dir + credentials
mkdir -p /var/tmp/team-kb-cicd
cp .env /var/tmp/team-kb-cicd/deploy.env        # from the env the live stack uses
chmod 600 /var/tmp/team-kb-cicd/deploy.env
# Append proxy vars so git/uv/npm/podman reach the outside:
env | grep -iE '^(https?_proxy|no_proxy|npm_config_proxy|npm_config_https_proxy)=' \
  >> /var/tmp/team-kb-cicd/deploy.env

# 2. Bootstrap (after this change is merged to main)
install -m 755 cicd/run.sh.template /var/tmp/team-kb-cicd/run.sh

# 3. systemd user units + linger (timers survive logout)
install -m 644 cicd/team-kb-cicd.service cicd/team-kb-cicd.timer \
  ~/.config/systemd/user/
systemctl --user daemon-reload
loginctl enable-linger "$USER"                   # verify: loginctl show-user "$USER" | grep Linger

# 4. Dry run against current origin/main (deploy withheld)
/var/tmp/team-kb-cicd/run.sh --dry-run           # or: systemctl --user start team-kb-cicd.service

# 5. Cutover deploy (see runbook below), then enable the timer
systemctl --user enable --now team-kb-cicd.timer  # verify: systemctl --user list-timers
```

## Cutover runbook (hand-run stack → pipeline-managed)

The stack was previously started by hand from the dev checkout; the pipeline
adopts it in one deliberate deploy:

1. Confirm nothing on the LAN used pi-agent directly at `host:8010`
   (host networking exposed it; compose publishes only `127.0.0.1:8010`).
   If something did, publish the port explicitly in `docker-compose.yml`.
2. Run the pipeline once with deploy enabled (or `systemctl --user start
   team-kb-cicd.service` after merging the pending work, so `origin/main`
   is at or ahead of the running stack):
   `podman compose up -d --remove-orphans` recreates the containers under
   the current compose wiring, carries the named volumes (`pgdata`,
   `neo4jdata`, `piagentdata`, `artifactsdata`), and removes
   `team-kb-pi-agent-host` (the old `--network host` workaround) as an orphan.
3. Verify: `curl http://127.0.0.1:8000/health` is green, a document ingested
   before the cutover is still searchable, and a pi-agent MCP round-trip
   succeeds (proves the in-network `webapp:8000` host trust).
4. Enable the timer (step 5 above). From here on, merges to `origin/main`
   deploy automatically within ~5 minutes.

## Rollback

```bash
# List deployed SHAs (newest last)
cat /var/tmp/team-kb-cicd/deployed-shas
/var/tmp/team-kb-cicd/repo/cicd/rollback.sh <sha>
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
