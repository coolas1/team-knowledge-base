## Context

The pipeline (`cicd/pipeline.sh`) is a single-branch, single-stack design:
`TKB_CICD_BRANCH` defaults to `main`, the stable dir `.deploy/` holds one
clone + one `deploy.env` + one state file, and `docker-compose.yml` hardcodes
the namespace (`name: team-kb`, `container_name: team-kb-*`, images
`team-kb-*`). Host ports are already env-driven (`APP_PORT`, `POSTGRES_PORT`,
`NEO4J_BOLT_PORT`, `NEO4J_HTTP_PORT`, `PI_AGENT_PORT`), volumes are
auto-prefixed by compose project name, `backup.sh` already accepts
`TKB_POSTGRES_CONTAINER`/`TKB_UPLOADS_VOLUME` overrides, and the pipeline's
self-update guard derives its home from script location — so two stable dirs
work without structural change to the sync logic. See proposal.md - Why.

The LAN host runs the production stack (137 GiB RAM available; images already
accumulate at 45 GB, 98% reclaimable). The dev checkout's `.env` points at the
production Postgres via `localhost:5433`, which is how 19 prod-DB-referenced
upload originals ended up in the checkout's untracked `uploads/`.

## Goals / Non-Goals

**Goals:**

- One independent pipeline instance per tracked branch: `main` (production,
  ports 8000/5433/7687/7474/8010) and `develop` (staging,
  8001/5434/7688/7475/8011), on one host.
- Full isolation between stacks: compose project, containers, images, volumes,
  networks, clones, state, gate venvs, backups.
- One compose file as the single source of truth, namespaced per deployment.
- Production untouched by the develop pipeline's failures and vice versa.
- Reconcile the stranded upload originals into the production volume before
  any cleanup of the dev checkout.

**Non-Goals:**

- Seeding the develop stack from production data (starts empty).
- Per-PR or ephemeral environments; more than two stacks.
- Changing the release process (`main` stays maintainer-released).
- Moving the pipeline off this host or off systemd user units.

## Decisions

### D1: Stable-dir layout — `.deploy/{main,develop}`, branch from deploy.env

Two peer stable dirs inside the gitignored `.deploy/` (the existing
`/.deploy/` gitignore entry already covers both children):

```
.deploy/
├── main/          ← moved from .deploy/ (repo/, deploy.env, backups/,
│                     last-deployed, deployed-shas, run.sh, pipeline.exec.sh)
└── develop/       ← new (same shape, its own everything)
      └── repo/…   each clone's pipeline.sh derives TKB_CICD_HOME from its
                   own location — two dirs need no extra wiring
```

Per-stack differences live in `deploy.env` only — the run.sh template and
pipeline stay single-source. New `deploy.env` keys: `TKB_CICD_BRANCH`
(main/develop) and `COMPOSE_PROJECT_NAME` (team-kb / team-kb-dev). Both
`run.sh` (initial clone `--branch`) and `pipeline.sh` read the branch from
`deploy.env` the same way they already read `APP_PORT`: a non-exported grep,
so app config never leaks into the gate's test environment.

*Alternative rejected:* separate `.deploy/` + `.deploy-dev/` — zero-migration
but permanently asymmetric; the user chose the peer layout.

### D2: One compose file, parameterized namespace

All hardcoded names become `${COMPOSE_PROJECT_NAME:-team-kb}-…`:

| Site | Today | After |
|---|---|---|
| `name:` | `team-kb` | `${COMPOSE_PROJECT_NAME:-team-kb}` |
| `container_name:` ×6 | `team-kb-postgres` … | `${COMPOSE_PROJECT_NAME:-team-kb}-postgres` … |
| `image:` ×4 | `team-kb-webapp` … | `${COMPOSE_PROJECT_NAME:-team-kb}-webapp` … |

Volumes need no change (compose prefixes them with the project name:
`team-kb_uploadsdata` vs `team-kb-dev_uploadsdata`). The main deployment
renders byte-identical names today (default fallback = current values), so
the production stack is not redeployed by this rename — no orphan churn.

`pipeline.sh` derives its SHA-tag list from the same env
(`compose_images=("${COMPOSE_PROJECT_NAME:-team-kb}-webapp" …)`), and
`rollback.sh`/`backup.sh` get the namespace the same way.

*Alternative rejected:* a second compose file — a duplicated ~280-line file
drifts immediately; the user chose one file, two envs.

### D3: Ports and stack wiring for develop

Develop `deploy.env` sets `APP_PORT=8001`, `POSTGRES_PORT=5434`,
`NEO4J_BOLT_PORT=7688`, `NEO4J_HTTP_PORT=7475`, `PI_AGENT_PORT=8011`
(all already env-substituted in compose; the pipeline's health URL already
follows `APP_PORT`). The develop stack starts with empty volumes — fresh
Postgres/Neo4j, migrations run on first startup. Everything else in develop's
`deploy.env` inherits main's behavioral overrides byte-for-byte
(`PI_AGENT_MAX_RUN_SECONDS=300`, tool-authoring disables, proxy and mirror
settings), changing only namespace and ports. External model endpoints
(LLM/embedding/reranker) are shared — both stacks are clients.

### D4: Separate gate venvs, shared npm cache and Node

`TKB_CICD_VENV` becomes per-stack (`/var/tmp/tkb-venvs/cicd` for main,
`/var/tmp/tkb-venvs/cicd-dev`): main and develop lockfiles diverge freely, and
a shared venv would `uv sync`-thrash between two heads every 5 minutes. The
npm cache (`npm_config_cache=/var/tmp/tkb-npm-cache`) is content-addressed —
shared safely. Each clone keeps its own `node_modules` (per-repo, as today).

### D5: Staggered timers

A second unit pair `team-kb-cicd-dev.{service,timer}` with the same 5-min
cadence but offset activation (`OnBootSec=4m30s` vs main's `2min`), so the
two pipelines interleave instead of racing for podman's storage locks on
every tick. Overlap is still safe (podman serializes on storage locks); the
stagger just keeps latency predictable.

### D6: Backups and image retention

Develop keeps pre-deploy backups with a small keep count
(`TKB_BACKUP_KEEP=2`); `backup.sh` already accepts container/volume name
overrides, which the develop `deploy.env` sets. New bounded image retention:
after deploy, prune SHA-tagged images beyond `TKB_IMAGE_KEEP` (default 10)
per stack, never removing the currently running or last-deployed tags —
addresses the 45 GB accumulation before doubling it.

### D7: Uploads reconciliation before cleanup

The 19 production-DB-referenced dirs are imported additively into the
production volume with `podman cp` per dir into `team-kb-webapp:/app/uploads/`
(additive — not the volume-replace path the restore runbook uses, since prod
must keep its existing 12 dirs). Verify by downloading one reconciled
document through the prod API, then delete the dev checkout's `uploads/`
(72 dirs) and root `node_modules/` (Vite cache only, zero packages).

### D8: Test uploads land in per-test temp dirs

`src/engine/graphrag/backend.py` binds `UPLOAD_DIR` at import time, so an
autouse conftest fixture monkeypatches `backend.UPLOAD_DIR` (and the
`UPLOADS_DIR` setting) to a `tmp_path` for the test session — the module
comment says the default stays relative for non-compose runs; only tests
change. This stops gate runs from dropping `uploads/<uuid>/week.md` into
every clone (21 accumulated in the current one).

## Risks / Trade-offs

- [`name:`/`container_name:` variable substitution behaves across compose
  versions] → verify with `podman compose --env-file <dev.env> config` (dry
  render) in a gate/checkable task before any deploy; substitution in these
  fields is documented docker-compose v2 behavior.
- [Migration window: main timer stopped while `.deploy/` moves] → minutes
  long, scripted, no compose objects touched (project stays `team-kb`);
  rollback = move the dir back + revert `ExecStart`.
- [Develop deploys broken code — staging stack unhealthy] → that is the
  stack's purpose; pipeline reports failure, production untouched.
- [Two full stacks double image/disk growth] → D6 retention bound.
- [Same secrets duplicated in two deploy.env files] → accepted: both live on
  the same host under the same 0600 stable-dir regime; per-stack data stores
  are separate so DB creds could later diverge per stack.
- [Reconciliation misimport (overwrite existing prod uploads)] → `podman cp`
  per dir is additive and the 19 dirs have zero overlap with the volume's 12
  (verified during exploration); verify a download before deleting the
  source.

## Migration Plan

1. Land the code change through the normal flow (PR to `develop`, release to
   `main`) — the parameterized pipeline ships through the old pipeline.
2. On the LAN host, stop `team-kb-cicd.timer`; move `.deploy/*` →
   `.deploy/main/`; install the updated run.sh at `.deploy/main/run.sh`;
   edit the unit's `ExecStart`; `daemon-reload`; dry-run main (gate+build,
   deploy withheld). Rollback: move back, revert `ExecStart`.
3. Create `.deploy/develop/` + its `deploy.env`; install run.sh; install the
   dev units; dry-run develop; start the dev timer → first deploy creates the
   staging stack (empty volumes) on :8001.
4. Reconcile uploads (D7), then remove `uploads/` and root `node_modules/`.
5. Restart the main timer; confirm both `GET /health` and `GET /version` on
   :8000 and :8001.

Rollback of the whole change: stop the dev timer, `podman compose -p
team-kb-dev down -v` (empty volumes — nothing to preserve), revert
`.deploy/main` to `.deploy/`.
