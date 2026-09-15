## Why

Merges to `develop` — the integration branch every feature lands on — have no
deployment: nothing exercises a merged stack until a maintainer cuts a release
to `main`, so integration breakage surfaces at release time, the worst moment
to find it. The pipeline already exists and is parameterizable at low cost, so
staging `develop` alongside production `main` is cheap insurance. Riding
along: exploration found that 19 production documents have their original
files only in the dev checkout's untracked `uploads/` (local dev servers share
the prod DB but write originals locally), so the planned root cleanup would
silently destroy production data without a reconciliation step first.

## What Changes

- **Dual pipeline**: a second pipeline instance watches `origin/develop` and
  deploys a staging stack, with its own systemd user timer/service
  (`team-kb-cicd-dev.*`), stable dir, disposable clone, deploy state, gate
  venv, and rollback history — all separate from the production pipeline.
- **Stable-dir restructure** (**BREAKING** for the host's systemd unit): the
  existing `.deploy/` moves to `.deploy/main/`, and the develop stable dir is
  `.deploy/develop/`; the production unit's `ExecStart` is updated once, with
  the timer stopped, preserving `deploy.env` byte-for-byte (it carries live
  overrides).
- **One compose file, two namespaces**: `docker-compose.yml` is parameterized
  by `COMPOSE_PROJECT_NAME` (project name, container names, image names);
  each stable dir's `deploy.env` supplies its namespace and host ports. The
  develop stack publishes +1 ports (`8001/5434/7688/7475/8011`) so the two
  stacks coexist on one host.
- **Isolation**: each stack gets its own volumes, networks, images, state, and
  pre-deploy backups (develop keeps a small backup history). The develop
  stack starts with empty volumes — no production seeding.
- **Uploads reconciliation**: import the 19 prod-DB-referenced upload dirs
  from the dev checkout into the production uploads volume (restore-runbook
  path), verify a download, then delete the dev checkout's `uploads/` and the
  inert root `node_modules/` (Vite cache only).
- **Test residue fix**: tests write upload originals to a per-test temp dir
  (`UPLOADS_DIR` → `tmp_path` in conftest), stopping the per-gate
  `week.md` residue that accumulates in pipeline clones.
- **Docs**: `cicd/README.md` and root `CLAUDE.md` updated for the dual layout,
  ownership rule, and install steps.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `local-cicd`: the pipeline's contract generalizes from "watch main, deploy
  one stack" to "one independent pipeline instance per tracked branch
  (main, develop), each owning a fully isolated stack". Adds isolation and
  dual-instance requirements; modifies the watch/backup/rollback/operator
  requirements to be per-stack.

## Impact

- **Repo**: `cicd/` (pipeline.sh, run.sh.template, backup.sh defaults, new dev
  systemd units, README), `docker-compose.yml` (namespace parameterization),
  `tests/conftest.py` (uploads temp dir), root `CLAUDE.md`.
- **LAN host (operational)**: one-time `.deploy/` → `.deploy/main/` migration
  with the production timer stopped; install of a second timer/service; a
  second full stack (Postgres, Neo4j, webapp, pi-agent) running — memory is
  not a constraint (~137 GiB available); image accumulation doubles, so a
  keep-last-N image prune rides along.
- **Production data**: uploads volume gains the 19 reconciled dirs; no other
  prod data is touched. The develop stack's empty volumes are new state only.
- **Not affected**: app runtime behavior, API contracts, `app-deployment`
  requirements (compose parameterization is naming only).
