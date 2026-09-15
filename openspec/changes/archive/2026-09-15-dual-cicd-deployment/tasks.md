## 1. Compose namespace parameterization

- [x] 1.1 Parameterize `docker-compose.yml` naming — `name:`, all six `container_name:`, all four `image:` values — to `${COMPOSE_PROJECT_NAME:-team-kb}-…` per design D2; verify `podman compose config` with no `COMPOSE_PROJECT_NAME` set renders byte-identical names to the current file (`team-kb`, `team-kb-postgres`, `team-kb-webapp`, …), so production is not redeployed by the rename
- [x] 1.2 Verify the dev namespace renders: `podman compose --env-file <dev-env> config` with `COMPOSE_PROJECT_NAME=team-kb-dev` shows `team-kb-dev` project, `team-kb-dev-*` containers/images, and auto-prefixed `team-kb-dev_*` volumes — zero name overlap with the main render
- [x] 1.3 Confirm the tool-runner service's `RUNNER_JOB_IMAGE` reference and the pi-agent in-network URLs (`webapp:8000`, `http://webapp:8000/mcp/`) need no change (network-internal names are per-project); verify via the rendered configs from 1.1/1.2

## 2. Pipeline per-stack wiring

- [x] 2.1 In `pipeline.sh`, read `TKB_CICD_BRANCH` and `COMPOSE_PROJECT_NAME` from `deploy.env` with the same non-exported grep used for `APP_PORT` (fallbacks `main` / `team-kb`), derive `compose_images` from the project name, and default `TKB_CICD_VENV` per stack (`/var/tmp/tkb-venvs/cicd` main, `cicd-dev` develop — overridable via deploy.env); verify a sandbox run (`TKB_CICD_HOME` pointed at a fixture stable dir with a develop-flavored deploy.env) logs the right branch, venv, and image names in watch/sync/gate/build stage headers
- [x] 2.2 Parameterize the initial clone branch in `run.sh.template` (read `TKB_CICD_BRANCH` from the stable dir's `deploy.env`, default `main`); verify a sandbox bootstrap clones the develop branch when the stable dir's deploy.env says `TKB_CICD_BRANCH=develop`
- [x] 2.3 Add bounded image retention as a post-deploy stage in `pipeline.sh`: prune `:<short-sha>`-tagged images beyond `TKB_IMAGE_KEEP` (default 10, settable in deploy.env), never removing the currently running or last-deployed tags; verify with a sandboxed podman fixture (fake tagged images) that old SHAs are pruned and the last two are kept, and that `--dry-run` skips pruning
- [x] 2.4 Extend `rollback.sh` to use the stack's `COMPOSE_PROJECT_NAME` for compose commands and image retagging (same deploy.env read); verify a sandbox rollback with a dev-namespace deploy.env touches only `team-kb-dev-*` images

## 3. Dev systemd units

- [x] 3.1 Add `cicd/team-kb-cicd-dev.service` and `team-kb-cicd-dev.timer` — copies of the main units with `ExecStart` at `.deploy/develop/run.sh`, staggered activation (`OnBootSec=4min30s`, same 5-min `OnUnitActiveSec`), and updated `Documentation=` path; verify `systemd-analyze verify` accepts both units and their schedules interleave with the main timer's

## 4. Test uploads land in temp dirs

- [x] 4.1 Add an autouse conftest fixture (root `tests/conftest.py`) that points upload-original storage at a session temp dir — monkeypatch `src.engine.graphrag.backend.UPLOAD_DIR` (bound at import) and the `UPLOADS_DIR` setting — per design D8; verify `uv run pytest` from a clean checkout leaves no `uploads/` directory behind and the full suite stays green

## 5. Docs

- [x] 5.1 Rewrite `cicd/README.md` for the dual layout: `.deploy/{main,develop}` tree diagram, per-stack `deploy.env` keys table (`TKB_CICD_BRANCH`, `COMPOSE_PROJECT_NAME`, ports +1, `TKB_BACKUP_KEEP=2`, `TKB_IMAGE_KEEP`), dev-unit install steps, the one-time main-stable-dir migration runbook, per-stack rollback, and the ownership rule restated per stack; verify every command in the install/migration sections is copy-pasteable against the new file layout
- [x] 5.2 Update root `CLAUDE.md` day-to-day notes: LAN deployment is two stacks (production :8000 from `main`, staging :8001 from `develop`), published ports for both; verify no stale reference to a single port/branch remains in the Workflow section

## 6. Repo validation & PR

- [ ] 6.1 Run the full validity check — `uv run ruff check`, `uv run pytest`, `cd src/frontend/webapp/client && npm test` — all green; confirm `git status` shows only intended tracked changes (no `uploads/`, `node_modules/`, `.deploy/` artifacts)
- [x] 6.2 Open the PR to `develop` carrying spec delta + code; after review/merge and maintainer release to `main`, confirm the existing pipeline deploys it without orphaning any `team-kb-*` container (parameterization is a no-op render for the main env)

## 7. Host migration & staging bring-up (post-release, LAN host)

- [ ] 7.1 Migrate the production stable dir with the timer stopped: `systemctl --user stop team-kb-cicd.timer`, move `.deploy/*` → `.deploy/main/` (preserving `deploy.env` byte-for-byte), install the updated run.sh at `.deploy/main/run.sh`, edit the main unit's `ExecStart`/`Documentation` to the new path, `daemon-reload`, then `.deploy/main/run.sh --dry-run`; verify the dry run passes gate+build with `last-deployed` unchanged (no redeploy churn) and production keeps serving on :8000 throughout
- [ ] 7.2 Create `.deploy/develop/` with its `deploy.env` (copy of main's behavioral overrides — `PI_AGENT_MAX_RUN_SECONDS=300`, tool-authoring disables, proxy/mirrors — plus `TKB_CICD_BRANCH=develop`, `COMPOSE_PROJECT_NAME=team-kb-dev`, `APP_PORT=8001`, `POSTGRES_PORT=5434`, `NEO4J_BOLT_PORT=7688`, `NEO4J_HTTP_PORT=7475`, `PI_AGENT_PORT=8011`, `TKB_BACKUP_KEEP=2`), `chmod 600`, install run.sh and the dev units, then `.deploy/develop/run.sh --dry-run`; verify the dry run gates and builds the develop head and deploys nothing
- [ ] 7.3 Start the dev timer for the first real staging deploy; verify `GET /health` and `GET /version` on :8001, an empty document list on staging, empty `team-kb-dev_*` volumes created, and production on :8000 untouched (same containers, data intact); record both stacks' versions
- [ ] 7.4 Restart the main timer and confirm interleaved steady state: both timers active, a no-op run on unchanged heads, and one forced develop redeploy (a trivial develop commit) exercises backup → deploy → verify → image-prune end-to-end on staging

## 8. Uploads reconciliation & root cleanup

- [ ] 8.1 Reconcile the stranded originals before any deletion: for each of the 19 production-DB-referenced dirs in the dev checkout's `uploads/`, `podman cp` the dir into `team-kb-webapp:/app/uploads/`; verify via the production API that a previously stranded document's original file downloads, and `ls /app/uploads` inside the container shows the new dirs alongside the existing 12
- [ ] 8.2 After 8.1 verification, remove the dev checkout's `uploads/` (72 dirs) and root `node_modules/` (Vite cache only); verify `git status` is unaffected (both untracked), a subsequent `uv run pytest` stays green, and no `uploads/` reappears (task 4.1 guard)
