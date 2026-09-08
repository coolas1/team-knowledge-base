## 1. Uploads persistence

- [x] 1.1 Add `uploads_dir` to `InfraSettings` (`config/settings.py`, env `UPLOADS_DIR`, default `"uploads"`) and replace the module-level `UPLOAD_DIR` in `src/engine/graphrag/backend.py` with the settings-driven path (module default preserved when unconfigured); verify with a unit test that an absolute `UPLOADS_DIR` is honored and the default keeps the relative behavior
- [x] 1.2 Add the `uploadsdata` named volume to `docker-compose.yml` (mount at `/app/uploads`, `UPLOADS_DIR=/app/uploads` env on the webapp service); verify `podman compose config` renders the mount and env
- [x] 1.3 After the pipeline deploys this change: upload a document through the BFF, trigger a redeploy (`run.sh --force`), and verify the original file still downloads and the document remains re-ingestable

## 2. Stable-dir relocation

- [x] 2.1 Make `pipeline.sh` derive its default `TKB_CICD_HOME` from the script location (clone root's parent) and `run.sh.template` derive its home from its own directory, both keeping the env override; verify sandbox run with `TKB_CICD_HOME` override still works and a run from a relocated clone picks up the sibling `deploy.env`
- [x] 2.2 Add `npm_config_cache=/var/tmp/tkb-npm-cache` to `deploy.env` (seeded template note in `cicd/README.md`); verify a gate run reads the cache dir (exists on ext4 after run)
- [x] 2.3 Update `cicd/team-kb-cicd.service` `ExecStart` to the repo-absolute `.deploy/run.sh` path; reinstall the unit and `daemon-reload`; verify `systemctl --user status team-kb-cicd.service` shows the new path
- [x] 2.4 Migrate the stable dir: move `deploy.env`, `last-deployed`, `deployed-shas` from `/var/tmp/team-kb-cicd` to `<repo>/.deploy/`, reinstall `run.sh` from the new template, move (or re-clone) the disposable clone; add `.deploy/` to `.gitignore`; verify a timer-fired run completes deploy-verified-healthy from the new location and `git status` stays clean
- [x] 2.5 Update `cicd/README.md` (layout table, install steps, runbook, npm-cache note, dev-checkout-wipe caveat, unit path); verify every path/command referenced matches the actual install

## 3. Repo hygiene

- [x] 3.1 Untrack and rename the benchmark output: `git rm -r --cached team-knowledge-base-files`, `mv team-knowledge-base-files bench`, gitignore `/bench/`; update the live reference in `docs/issues.md`; verify `git status` shows the removal only and `bench/` is ignored

## 4. End-to-end verification

- [x] 4.1 Push all changes through the review flow; after the pipeline deploys the merged commit, verify: uploaded-file survival across a `--force` redeploy (task 1.3), the journal shows the deploy from `.deploy/`, volumes (now including `uploadsdata`) all predate the redeploy, and `git status` in the dev checkout stays clean throughout
