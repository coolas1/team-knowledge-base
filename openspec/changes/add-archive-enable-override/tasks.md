## 1. Configuration resolution

- [x] 1.1 Add `enabled: bool | None = None` to `ArchiveSettings` in
  `config/settings.py`, declared like the other knobs.
  Verify: `ARCHIVE_ENABLED=true uv run python -c "from config.settings import
  settings; print(settings.archive.enabled)"` prints `True`, and the same
  command without the variable prints `None` (not `False` — the sentinel is
  what makes D2 work).
- [x] 1.2 Resolve it in `merge_archive_config`
  (`src/engine/components/archive/config.py`) as
  `env.enabled if env.enabled is not None else cfg.enabled`.
  Verify: tests cover all four combinations — env true, env false, env unset
  with `app.yaml` false, env unset with `app.yaml` true.
- [x] 1.3 Guard the sentinel against a future "simplification" to
  `bool = False`.
  Verify: a test named for the behaviour asserts that an unset
  `ARCHIVE_ENABLED` with `ArchiveCfg(enabled=True)` still resolves enabled,
  and that an explicit `ARCHIVE_ENABLED=false` overrides `app.yaml`'s true.

## 2. Container plumbing

- [x] 2.1 Add `ARCHIVE_ENABLED:` to the webapp service `environment` map in
  `docker-compose.yml`, beside the existing `ARCHIVE_*` passthroughs.
  Verify: rendering the compose config with `ARCHIVE_ENABLED=true` in the
  environment shows the key inside the webapp service's environment, not
  merely as a substitution value.

## 3. Documentation

- [x] 3.1 Add an `### archive` section under `## config/app.yaml` in
  `docs/config-reference.md`, documenting the archive keys and their
  `ARCHIVE_*` overrides, stating that the committed default is off and that
  enabling is a per-deployment act.
  Verify: every key present in the `archive:` block of `config/app.yaml`
  appears in the section, and `ARCHIVE_ENABLED` is named.

## 4. Validation and PR

- [x] 4.1 `uv run ruff check` passes with no new findings.
- [x] 4.2 `uv run pytest` passes (unit + contract + BFF).
- [ ] 4.3 Push the branch and open a PR into `develop` using
  `.github/PULL_REQUEST_TEMPLATE.md`, carrying this change's spec delta.

## 5. Staging enablement and trial (operator)

- [ ] 5.1 After the PR merges and the staging pipeline redeploys, add
  `ARCHIVE_ENABLED=true` to the staging stack's `.deploy/develop/deploy.env`.
  Verify: the file keeps mode 600 and the key appears exactly once.
- [ ] 5.2 Redeploy staging and confirm the pipeline starts.
  Verify: `podman exec team-kb-dev-webapp env | grep ARCHIVE_ENABLED` reports
  `true`, and `curl -s -o /dev/null -w '%{http_code}'
  http://localhost:8001/api/archive/operations` returns something other than
  the `archive_disabled` 503.
- [ ] 5.3 Confirm production is untouched by the whole change.
  Verify: `curl -s -o /dev/null -w '%{http_code}'
  http://localhost:8000/api/archive/operations` still returns the
  `archive_disabled` 503 envelope, and production's `/version` commit is
  unchanged from before this change.
