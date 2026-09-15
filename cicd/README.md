# Local CI/CD pipeline (dual stack)

Automates the path from accepted code to the running LAN deployments with
**two independent pipeline instances on one host**:

| Stack | Watches | Branch role | API | Postgres | Neo4j bolt/http | pi-agent |
|-------|---------|-------------|-----|----------|-----------------|----------|
| production | `origin/main` | maintainer-released | :8000 | :5433 | :7687 / :7474 | :8010 |
| staging | `origin/develop` | integration branch | :8001 | :5434 | :7688 / :7475 | :8011 |

Each instance is a systemd **user** timer polling every 5 minutes; on a new
commit it runs watch → sync → gate → build → backup → deploy → verify →
prune on its own stack. The two stacks share one compose definition
(`docker-compose.yml`) but occupy separate namespaces (compose project,
containers, images, volumes, networks) derived from each stack's
`COMPOSE_PROJECT_NAME` — a deploy on one stack never touches the other's
objects.

```
~/.config/systemd/user/team-kb-cicd.timer      (5-min, OnBootSec=2min)
  └─ team-kb-cicd.service → <repo>/.deploy/main/run.sh      (static bootstrap)
                              └─ <repo>/.deploy/main/repo/cicd/pipeline.sh
~/.config/systemd/user/team-kb-cicd-dev.timer  (5-min, OnBootSec=4min30s)
  └─ team-kb-cicd-dev.service → <repo>/.deploy/develop/run.sh
                                └─ <repo>/.deploy/develop/repo/cicd/pipeline.sh
```

`<repo>` is the dev checkout: `/home/zhangxiang/workspaces/projects/team-knowledge-base`.
The stable dirs live under the gitignored `.deploy/` — they never show in
git status or commits. Both `run.sh` and `pipeline.sh` derive their stable
dir from their own location (`TKB_CICD_HOME` overrides it for sandbox
testing), so the whole `.deploy/` tree moves with the repo; only the units'
`ExecStart` paths are absolute.

## Layout

```
.deploy/
├── main/                 ← production stack (watches main)
│   ├── deploy.env        ← credentials + namespace + ports + proxy
│   ├── run.sh            ← static bootstrap (from cicd/run.sh.template)
│   ├── repo/             ← disposable clone (git fetch + reset --hard)
│   ├── last-deployed     ← SHA of the last successful deploy
│   ├── deployed-shas     ← history: <timestamp> <sha> per deploy/rollback
│   ├── backups/          ← pre-deploy backup sets (keep TKB_BACKUP_KEEP)
│   └── pipeline.exec.sh  ← snapshot of the running pipeline (self-update)
└── develop/              ← staging stack (watches develop), same shape
```

Also on local disk (the home filesystem is slow cephfs):

| Path | Role |
|------|------|
| `/var/tmp/tkb-venvs/cicd` | Production gate venv (`UV_PROJECT_ENVIRONMENT`). Per stack: the two lockfiles diverge freely, and a shared venv would `uv sync`-thrash between two heads every 5 minutes. |
| `/var/tmp/tkb-venvs/cicd-dev` | Staging gate venv. |
| `/var/tmp/tkb-npm-cache` | SPA npm cache (`npm_config_cache` in deploy.env) — content-addressed, shared safely by both gates. |
| `/var/tmp/node22/bin` | Node 22 for the SPA tests (system Node is 18). |

Perf note: only the clones (git objects, source tree, `node_modules`) live
on cephfs. The gate venvs, npm cache, and podman storage stay on local
disk, so the 5-min cadence is not cephfs-bound; a fresh clone's first
`npm install` is slower (~5 min observed), later runs reuse `node_modules`
(`clean -fd` keeps ignored files).

**Caveat:** because `.deploy/` lives inside the dev checkout, wiping the
checkout (e.g. `git clean -fdx` from the parent, or deleting the directory)
also wipes both stable dirs. Volumes, images, and the systemd units
survive; restore `deploy.env` from the source `.env` plus the
proxy/`npm_config_cache` lines (migration runbook step 1 below) and re-run
step 2 for each stack. `deploy.env` also carries `PI_AGENT_MAX_RUN_SECONDS=300`
(the LAN LLM backend's mean request time is ~128s, so the compose default
of 180s kills healthy multi-iteration turns) — the setting lives in the
source `.env` too, so a plain restore keeps it; do not drop it back to 180.

## Per-stack `deploy.env`

Both `deploy.env` files share the same credential/proxy keys (secrets are
duplicated by design: same host, same 0600 stable-dir regime; per-stack
data stores are separate so DB creds could later diverge). The keys that
differ per stack:

| Key | production (`.deploy/main/`) | staging (`.deploy/develop/`) |
|-----|------------------------------|------------------------------|
| `TKB_CICD_BRANCH` | `main` | `develop` |
| `COMPOSE_PROJECT_NAME` | `team-kb` | `team-kb-dev` |
| `APP_PORT` | `8000` | `8001` |
| `POSTGRES_PORT` | `5433` | `5434` |
| `NEO4J_BOLT_PORT` | `7687` | `7688` |
| `NEO4J_HTTP_PORT` | `7474` | `7475` |
| `PI_AGENT_PORT` | `8010` | `8011` |
| `TKB_BACKUP_KEEP` | `5` (default) | `2` |
| `TKB_IMAGE_KEEP` | `10` (default) | `10` (default) |

Other keys read from `deploy.env` (all optional, defaults in parentheses):
`TKB_CICD_VENV` (per-branch default under `/var/tmp/tkb-venvs/`), proxy
vars (`https_proxy` etc., exported to the pipeline), `npm_config_cache`,
`PYPI_MIRROR`/`NPM_REGISTRY`/`NPM_AUDIT_REGISTRY`/`NPM_PROXY` (build args).

Behavioral overrides (`PI_AGENT_MAX_RUN_SECONDS=300`, tool-authoring
disables, mirrors) are copied byte-for-byte between the two files — only
namespace/ports/retention differ.

## Self-update

`pipeline.sh` lives inside the clone it syncs, so a run always *starts* from
the previous head's copy of the script. Two guards make that safe: the
script snapshots itself to the stable dir before doing anything (`git reset
--hard` must never rewrite the file bash is executing), and after sync it
compares itself against the freshly checked-out `cicd/pipeline.sh` and
re-execs the new version when they differ — so a change to the pipeline
lands on the very run that pulls it. Each instance re-execs only within its
own stable dir. `rollback.sh` snapshots itself the same way (it resets the
clone to the target SHA mid-run).

## One-time migration (single → dual layout)

Moves the existing production stable dir to `.deploy/main/` and installs the
staging instance. Run with the production timer stopped; the whole window is
minutes long and touches no compose objects (the project stays `team-kb`).
Rollback of the migration: move the dir back and revert `ExecStart`.

```bash
cd /home/zhangxiang/workspaces/projects/team-knowledge-base

# 1. Stop the production timer (production keeps serving; only the pipeline pauses)
systemctl --user stop team-kb-cicd.timer

# 2. Move the stable dir (deploy.env is preserved byte-for-byte)
mkdir -p .deploy/main
mv .deploy/repo .deploy/deploy.env .deploy/last-deployed \
   .deploy/deployed-shas .deploy/backups .deploy/run.sh .deploy/pipeline.exec.sh \
   .deploy/main/

# 3. Install the updated bootstrap (reads TKB_CICD_BRANCH from deploy.env)
install -m 755 cicd/run.sh.template .deploy/main/run.sh

# 4. Point the production unit at the new path
systemctl --user edit --full team-kb-cicd.service   # or edit the installed file:
#   ExecStart=/home/zhangxiang/workspaces/projects/team-knowledge-base/.deploy/main/run.sh
#   Documentation=file:///home/zhangxiang/workspaces/projects/team-knowledge-base/.deploy/main/repo/cicd/README.md
systemctl --user daemon-reload

# 5. Dry run (gate+build; deploy withheld, last-deployed untouched)
.deploy/main/run.sh --dry-run

# 6. (Optional now, done below with the dev install) restart the timer
```

## Install (staging instance, one-time)

```bash
cd /home/zhangxiang/workspaces/projects/team-knowledge-base

# 1. Stable dir + credentials: copy main's behavioral overrides, then set
#    the staging namespace/ports/retention (see the table above).
mkdir -p .deploy/develop
cp .deploy/main/deploy.env .deploy/develop/deploy.env
chmod 600 .deploy/develop/deploy.env
# Edit .deploy/develop/deploy.env:
#   TKB_CICD_BRANCH=develop
#   COMPOSE_PROJECT_NAME=team-kb-dev
#   APP_PORT=8001
#   POSTGRES_PORT=5434
#   NEO4J_BOLT_PORT=7688
#   NEO4J_HTTP_PORT=7475
#   PI_AGENT_PORT=8011
#   TKB_BACKUP_KEEP=2

# 2. Bootstrap (the initial clone follows TKB_CICD_BRANCH from deploy.env)
install -m 755 cicd/run.sh.template .deploy/develop/run.sh

# 3. systemd user units (staggered 4min30s after boot vs main's 2min)
install -m 644 cicd/team-kb-cicd-dev.service cicd/team-kb-cicd-dev.timer \
  ~/.config/systemd/user/
systemctl --user daemon-reload

# 4. Dry run against current origin/develop (deploy withheld)
.deploy/develop/run.sh --dry-run

# 5. First real deploy creates the staging stack (empty volumes) on :8001
systemctl --user start team-kb-cicd-dev.service   # or: .deploy/develop/run.sh
curl http://127.0.0.1:8001/health

# 6. Enable the timers (both stacks now deploy automatically within ~5 min)
systemctl --user enable --now team-kb-cicd.timer team-kb-cicd-dev.timer
loginctl enable-linger "$USER"                   # verify: loginctl show-user "$USER" | grep Linger
```

The staging stack starts with **empty data stores** — fresh Postgres/Neo4j
volumes, no production seeding. Do not copy production data into it.

The units' `ExecStart` paths are absolute; if the checkout moves, edit
`ExecStart` (and `Documentation=`) in both units and
`systemctl --user daemon-reload`.

## Rollback (per stack)

```bash
# List a stack's deployed SHAs (newest last)
cat .deploy/main/deployed-shas        # production
cat .deploy/develop/deployed-shas     # staging

# Roll that stack back to a previously deployed SHA
.deploy/main/repo/cicd/rollback.sh <sha>          # production
.deploy/develop/repo/cicd/rollback.sh <sha>       # staging
```

Rollback retags that stack's SHA-tagged images as `:latest`, checks its
clone out at that SHA (so the compose file matches), and redeploys — only
the stack's own `COMPOSE_PROJECT_NAME`-prefixed images are touched. It
deliberately does not rewrite `last-deployed`: while the watched branch
stays put, that stack's timer no-ops and the rollback sticks; the next
merge deploys forward again. To pin a rollback longer, stop that stack's
timer (`systemctl --user stop team-kb-cicd.timer` / `team-kb-cicd-dev.timer`).

## Backups

Every real deploy runs `cicd/backup.sh` **before** `podman compose up`
replaces the stack (`--dry-run` skips it, so a dry run never touches
deployment data). Each backup writes a dated set to
`<stable-dir>/backups/<sha>-<timestamp>/`:

- `postgres.sql.gz` — `pg_dump --clean --if-exists` of the app database
  (documents, chunks, vectors, memory rows);
- `uploads.tar.gz` — snapshot of that stack's `uploadsdata` volume
  (original uploaded files);
- `deploy.env.snapshot`, `commit` — the config and SHA the backup precedes.

The most recent `TKB_BACKUP_KEEP` sets are kept per stack (production
default 5, staging 2); older ones are removed. The container/volume names
come from the stack's `deploy.env` (`TKB_POSTGRES_CONTAINER` /
`TKB_UPLOADS_VOLUME` are supported overrides). Tune `TKB_BACKUP_KEEP` in
`deploy.env`. The script header carries the single-operator rule: it
touches the deployment's containers/volumes, so run it only via the
pipeline (or a deliberate restore drill), never by hand in the dev
checkout.

### Restore from a backup

Restore into the **same stack** the backup came from, with that stack's
timer stopped so it does not redeploy mid-restore. Production example:

```bash
systemctl --user stop team-kb-cicd.timer
cd .deploy/main/repo                    # the clone the pipeline manages
backup=../backups/<sha>-<ts>

# 1. Postgres: replace current data with the dump.
zcat "$backup/postgres.sql.gz" \
  | podman exec -i team-kb-postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"

# 2. Uploads volume: recreate it from the snapshot, then restart the webapp.
podman volume rm team-kb_uploadsdata          # only after the dump is restored
podman volume create team-kb_uploadsdata
zcat "$backup/uploads.tar.gz" | podman volume import team-kb_uploadsdata -

podman compose --env-file ../deploy.env up -d --remove-orphans
systemctl --user start team-kb-cicd.timer     # resume the pipeline
```

For the staging stack, stop `team-kb-cicd-dev.timer`, work under
`.deploy/develop/repo`, and use the `team-kb-dev`-prefixed names
(`team-kb-dev-postgres`, `team-kb-dev_uploadsdata`).

Verify by searching for a document indexed before the backup and downloading
its original file from the doc detail page; the uploads snapshot and the dump
come from the same pre-deploy instant, so they agree.

## Image retention

Every build tags its images `:<short-sha>` alongside `:latest`, so any
deployed SHA stays redeployable without a rebuild — with two stacks that
accumulation doubles. After each successful deploy the pipeline prunes
SHA-tagged images older than the newest `TKB_IMAGE_KEEP` entries of that
stack's deploy history (default 10; `0` disables pruning). `:latest` — the
tag the running containers use — and the newest keep-set (the recorded
rollback paths) are never removed. Tune `TKB_IMAGE_KEEP` in `deploy.env`.

## Memory capability rollout

Release these additive memory changes through the pipeline gate and image
build. Do not run schema or compose changes manually on either stack.

1. Keep every new memory feature flag disabled while the new image starts. The
   startup migration adds scope, operation, observation, model and directive data
   without deleting legacy rows. Confirm `/health` and `/api/memory/operations`.
2. Enable the dependency chain for a test scope in order: `scope`,
   `reliable_retention`, `consolidation`, `retrieval`, `mental_models`, then
   `adaptive_reflect`. Keep consolidation and model concurrency at 1 for the first
   watermark. Invalid combinations fail configuration validation before serving.
3. Follow a completed test turn in the memory page or
   `/api/memory/operations?session_id=...&turn_id=...`. Confirm retain,
   consolidation and optional model-refresh stages, then verify an observation's
   current sources and history. Run the resumable historical delivery/backfill
   command only with an explicit start point and test scope.
4. Expand scope traffic only after pending/failed counts stabilize and deleted
   test evidence cannot be expanded. The pipeline health check remains the deploy
   boundary; no management action is a substitute for it.

For a compatible rollback, first disable `adaptive_reflect`, the model worker and
the consolidation worker, then disable their feature flags in reverse dependency
order. Use that stack's `rollback.sh <sha>` with a scope-aware preceding image.
Preserve new tables, operation rows, observation history, queued watermarks and
tombstones. Never roll back to a reader that ignores scope or deletion tombstones
after isolated traffic has been enabled. Retry pending work after rolling forward.

## Ownership rule (per stack)

**Each pipeline instance is the sole operator of its compose project.**
Never run `podman compose up`/`down` from the dev checkout or by hand for
either project (`team-kb` or `team-kb-dev`): compose fixes the project name
and container names from `COMPOSE_PROJECT_NAME`, so a second operator
collides with that stack's pipeline. The dev checkout is a *client* of both
stacks — reach them only through the published ports:

- production: Postgres `5433`, Neo4j `7687`, webapp `8000`
- staging: Postgres `5434`, Neo4j `7688`, webapp `8001`

## Failure semantics

- Gate or build failure ⇒ non-zero exit before deploy; that stack keeps
  serving its last good version; the failure is visible in
  `systemctl --user status team-kb-cicd[-dev]` and
  `journalctl --user -u team-kb-cicd[-dev].service`.
- Unhealthy after deploy ⇒ non-zero exit (the new version stays up; run that
  stack's rollback command if needed).
- Every successful build is tagged `:<short-sha>` alongside `:latest`, so
  rollback needs no rebuild.
- A failure on one stack never affects the other's containers, images,
  volumes, or deployed version.

`RUN_INTEGRATION=1` tests are deliberately **not** in the gate — they point
at the production backing services.
