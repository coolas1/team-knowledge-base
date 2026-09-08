## Why

Three follow-ups from the cutover review: (1) uploaded document files are
written to a relative `uploads/` path inside the container — not on a volume
— so every pipeline redeploy silently wipes them (the cutover already lost
the pre-existing originals; chunks/metadata in Postgres survive, so docs
stay listed and searchable but their files are gone); (2) the deployment's
stable dir lives in `/var/tmp/team-kb-cicd`, which systemd tmpfiles may
age-clean (~30d unused) and which is invisible from the repo; (3)
`team-knowledge-base-files/` is untracked-worthy benchmark output tracked in
git under a long, meaningless name.

## What Changes

- Persist uploaded document files: a settings-driven absolute uploads dir
  (`UPLOADS_DIR`, default keeps current relative behavior) plus a named
  compose volume `uploadsdata:/app/uploads` on the webapp service. Files
  uploaded after this change survive every redeploy. Pre-cutover documents
  keep their chunks/metadata (searchable) but their original files are
  unrecoverable — **re-uploading accepted**, no data migration.
- Relocate the CI/CD stable dir from `/var/tmp/team-kb-cicd` to a
  gitignored `.deploy/` under the repo: `deploy.env`, `run.sh`, the
  disposable clone, and the state files move there; systemd unit `ExecStart`
  is updated. The pipeline derives its stable dir from its own location
  instead of a hardcoded path. Perf-critical caches stay on local disk
  (gate venv, npm cache, podman storage). Trade-off: the clone now sits on
  cephfs (slower first `npm install`; `/var/tmp` tmpfiles-cleaning risk is
  gone; wiping the dev checkout would now lose `deploy.env` — documented).
- Repo hygiene: untrack `team-knowledge-base-files/` (benchmark QA output),
  gitignore it, and rename it to `bench/`. The historical name remains in
  archived openspec changes (untouched history); live references
  (`docs/issues.md`) are updated.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `app-deployment`: uploaded document files SHALL be stored at a
  configured absolute path, on a mounted volume in the compose deployment,
  so they survive container recreation.
- `local-cicd`: the persisted-data requirement now includes the uploaded
  documents volume; the credentials-isolation requirement is restated for
  the stable dir living inside the (gitignored) dev-repo tree, outside the
  disposable clone.

## Impact

- **Code**: `src/engine/graphrag/backend.py` (`UPLOAD_DIR` becomes a
  settings-driven path), `config/settings.py` (new `uploads_dir` setting),
  `docker-compose.yml` (new `uploadsdata` volume + `UPLOADS_DIR` env).
- **cicd/**: `pipeline.sh` / `run.sh.template` / `team-kb-cicd.service`
  (stable-dir location, self-derivation), `README.md` (paths, npm cache,
  relocation runbook); one-time migration of the existing
  `/var/tmp/team-kb-cicd` content.
- **Repo**: `.gitignore` (`.deploy/`, `bench/`), untrack + rename
  `team-knowledge-base-files/` → `bench/`, update `docs/issues.md`.
- **Systems**: no dependency changes; running stack needs one pipeline
  deploy to gain the uploads volume.
