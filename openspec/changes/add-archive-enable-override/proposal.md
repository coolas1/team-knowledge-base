## Why

`archive.enabled` is the one auto-archiving knob that cannot be set per
deployment. `merge_archive_config()` reads it straight from `config/app.yaml`
(`enabled=cfg.enabled`) while every other knob — threshold, delta,
`review_all`, `poll_seconds`, `stability_checks`, `max_attempts`, `top_k`,
`collision_policy` — falls back to an `ARCHIVE_*` environment variable.

Because `config/app.yaml` is baked into the image, the only way to turn the
feature on today also turns it on in production at the next release. That is
the wrong shape for a pipeline that executes real file moves unattended once
confidence clears the threshold (0.75), and which has not yet run in any
environment. The feature needs a staging trial with production left
deliberately off.

## What Changes

- Add an `enabled` field to `ArchiveSettings` so `ARCHIVE_ENABLED` overrides
  the `app.yaml` value, matching the precedence every other knob already has.
- Pass `ARCHIVE_ENABLED` through to the webapp container in
  `docker-compose.yml`, alongside the existing `ARCHIVE_*` passthroughs.
- Keep `config/app.yaml` at `enabled: false` as the committed default, so a
  deployment opts in rather than inheriting the feature.
- Document the archive section and its environment overrides in
  `docs/config-reference.md`, which currently documents neither.
- Operator step, outside the repo: set `ARCHIVE_ENABLED=true` in the staging
  stack's untracked `.deploy/develop/deploy.env`.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `app-deployment`: whether the auto-archiving pipeline runs becomes an
  environment-injectable, per-deployment setting rather than a value fixed by
  the committed application configuration.

## Impact

**Config** — `config/settings.py` (`ArchiveSettings`, env prefix `ARCHIVE_`),
`src/engine/components/archive/config.py` (`merge_archive_config`).

**Compose** — `docker-compose.yml`, webapp service `environment` block (the
existing `ARCHIVE_*` entries, which currently omit the enable switch).

**Docs** — `docs/config-reference.md`; the `## config/app.yaml` section
documents `engine` and `plugin` but has no `archive` section.

**Tests** — `tests/engine/test_archive.py` already covers
`merge_archive_config` defaults and env precedence; those cases extend to the
new field.

**Behaviour** — no change to a deployment that sets nothing: the committed
default remains off, and `ARCHIVE_ENABLED` unset preserves today's resolution.
A deployment that sets `ARCHIVE_ENABLED=true` starts the archive scanner and
worker at startup; `false` suppresses them even when `app.yaml` says
otherwise. **Dependencies**: none added. **Breaking changes**: none.

**Note on the spec home.** `app-deployment` exists under `openspec/specs/`,
but the archiving requirements themselves are still pending in the not-yet-
archived `add-auto-file-archiving` change — its `app-deployment` delta is
where the "archive configuration is injectable via environment" requirement
currently lives, and it enumerates thresholds, band width, poll interval and
mode while omitting the enable switch. This change adds a requirement to the
same capability to close that gap; the two fold in on archive.
