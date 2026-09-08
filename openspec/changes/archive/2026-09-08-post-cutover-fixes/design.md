## Context

See proposal.md — Why. Facts that shape this design:

- `UPLOAD_DIR = Path("uploads")` (`src/engine/graphrag/backend.py:44`) is
  relative; the webapp container's WORKDIR is `/app`, so uploads land in the
  container filesystem. The compose webapp mounts only `artifactsdata`
  (`/app/artifacts`), HuggingFace cache, nothing for uploads — verified in
  the live container: `uploads/` empty after the cutover recreated it.
- The stable dir `/var/tmp/team-kb-cicd` (deploy.env, run.sh, disposable
  clone, `last-deployed`, `deployed-shas`) is on local ext4 by the original
  design decision ("cephfs is slow"), but `/var/tmp` is subject to systemd
  tmpfiles age-based cleaning (~30d unused); the timer's 5-min touching of
  the *timer unit* does not touch the directory itself, so the risk is real
  but unproven.
- `/home` is cephfs: the dev checkout venv is already relocated to
  `/var/tmp/tkb-venvs/tkb` for speed; an SPA `npm install` on cephfs took
  >5 min vs ~1 min on `/var/tmp` (observed 2026-09-07).
- The repo working tree at `/home/zhangxiang/workspaces/projects/team-knowledge-base`
  is the deployment host's dev checkout; `team-knowledge-base-files/qa/`
  holds the 2026-09-03 retrieval-benchmark output (16 tracked files).

## Goals / Non-Goals

**Goals:**

- Uploaded files survive every redeploy from now on.
- The deployment (stable dir) lives in the repo tree, gitignored, immune to
  `/var/tmp` cleaning, discoverable next to the code it deploys.
- Gate stays fast enough for the 5-min cadence: heavy caches remain on
  local disk.
- Benchmark output leaves git under a short, meaningful name.

**Non-Goals:**

- Recovering the pre-cutover original files (re-uploading accepted).
- Migrating uploaded files between locations (none exist yet under the new
  volume).
- Moving podman storage, the gate venv, or Node 22 (perf-critical, already
  correct).
- Making the stable dir location configurable at install time beyond the
  documented default.

## Decisions

1. **Uploads path: settings-driven absolute dir, default unchanged.** Add
   `uploads_dir` to `InfraSettings` (env `UPLOADS_DIR`, default `"uploads"`
   — keeps dev-checkout behavior identical). Compose sets
   `UPLOADS_DIR=/app/uploads` and mounts a new named volume
   `uploadsdata:/app/uploads`. Alternatives rejected: hardcoding an absolute
   path in the engine (breaks dev); mounting over the relative path's
   resolved location without an env (fragile against WORKDIR changes).

2. **Stable dir: `<repo>/.deploy/`, derived, not hardcoded.** `run.sh`
   computes its home from its own location; `pipeline.sh` defaults
   `TKB_CICD_HOME` to the clone root's parent (script at
   `.deploy/repo/cicd/pipeline.sh` → `.deploy/`), keeping the env override
   for sandbox use. The systemd unit's `ExecStart` points at the absolute
   `.deploy/run.sh` path. Alternatives rejected: staying on `/var/tmp`
   (tmpfiles risk, invisible from repo); keeping the hardcoded path and
   just moving the dir (brittle if the repo moves).

3. **Perf caches stay on local disk.** The gate venv stays at
   `/var/tmp/tkb-venvs/cicd`; `npm_config_cache=/var/tmp/tkb-npm-cache` is
   added to `deploy.env` so the SPA install reads/writes its cache on ext4
   even though `node_modules` now lives on cephfs. Only the clone itself
   (git objects, source tree, `node_modules`) moves to cephfs; first
   install after a clone wipe is slower, subsequent runs reuse
   `node_modules` (`clean -fd` keeps ignored files). Alternatives rejected:
   symlinking `node_modules` out of the clone (fragile, surprising);
   keeping the whole stable dir on `/var/tmp` (rejected in decision 2).

4. **Benchmark output: untrack, gitignore, rename to `bench/`.**
   `git rm -r --cached team-knowledge-base-files`, rename on disk to
   `bench/`, gitignore `/bench/`. Name is short and self-describing (holds
   `qa/` results; future benchmark outputs have an obvious home).
   References in live docs (`docs/issues.md`) updated; mentions inside
   `openspec/changes/archive/` stay as-is (historical record).

5. **Migration is repo-side only for the stable dir; one pipeline deploy
   applies the uploads volume.** Moving `.deploy/` does not touch
   containers. The uploads volume appears on the next pipeline deploy of
   this change (`up -d` creates the volume; empty at first).

## Risks / Trade-offs

- [Wiping the dev checkout now also wipes `deploy.env` and deploy state]
  → accepted (user's choice); volumes/images/units survive, so only
  credentials need restoring from the source `.env`; documented in README.
- [cephfs clone slows the gate: first SPA install per fresh clone ~5+ min]
  → npm cache on ext4 (decision 3); `node_modules` persists across runs;
  systemd oneshot + timer never overlap runs, so a slow gate only delays
  the next deploy.
- [Pre-cutover docs (37) have no original files: download/edit/reingest
  fail for them] → accepted per proposal (re-upload); search unaffected
  (chunks in Postgres).
- [`.deploy` inside the repo could be accidentally committed]
  → gitignored `/`. entry plus the spec'd "never in git status" scenario;
  `git status` review before pushes is existing habit.
- [Unit `ExecStart` hardcodes the absolute repo path] → documented in the
  runbook; a moved checkout requires a unit edit + `daemon-reload` (rare).

## Migration Plan

1. Land code changes through the normal flow (develop → review → main).
2. On the LAN host: `git rm -r --cached` + rename benchmark dir; move
   `/var/tmp/team-kb-cicd` content to `<repo>/.deploy/` (deploy.env, state
   files, run.sh reinstalled from the new template, clone moved or
   re-cloned); update + reload the systemd unit; `gitignore` entries.
3. Next timer run deploys the uploads volume; verify by uploading a
   document and redeploying (`--force`), then confirming the file still
   downloads.
4. Rollback: standard pipeline rollback (prior SHA image); the `.deploy/`
   move is reversible by moving the directory back and reverting the unit.

## Open Questions

- None blocking. (Whether to also publish a `bench/` README is cosmetic and
  can be decided at implementation.)
