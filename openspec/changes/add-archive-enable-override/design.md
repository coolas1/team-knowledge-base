## Context

See proposal.md — Why. The constraints that shape the approach:

**The resolution chain today.** `merge_archive_config(app)` in
`src/engine/components/archive/config.py` builds the effective config from
two sources: `app.archive` (pydantic model `ArchiveCfg` in `config/schema.py`,
loaded from `config/app.yaml`) and `settings.archive` (pydantic-settings
`ArchiveSettings` in `config/settings.py`, `env_prefix="ARCHIVE_"`, reading
`.env`). Every knob is written as `env.X if env.X is not None else cfg.X`.
Only `enabled` breaks the pattern: it is `enabled=cfg.enabled`, so its value
comes from `app.yaml` alone.

**Why that is not just an omission.** `ArchiveSettings` declares every
overridable knob as `X | None = None`, and `None` is the sentinel meaning
"the environment expressed no opinion, defer to app.yaml". `enabled` has no
such field, so there is no way to express "no opinion" for it and therefore
no way to add an override that composes with `app.yaml`. Adding the field is
not sufficient on its own — the sentinel has to be preserved, or the override
silently becomes unconditional.

**How a per-stack value reaches the container.** Two hops, and both matter.
The pipeline passes the stable dir's `deploy.env` to compose with
`--env-file`, which supplies *substitution* values for `${VAR}` in the
compose file; the pipeline deliberately does not export application config
into its own shell (only proxy keys are exported). The webapp service then
lists an explicit `environment:` mapping — that list is what actually reaches
the container's process environment, which is what pydantic-settings reads.
A variable that is present in `deploy.env` but absent from that list would
substitute into the compose file and never reach the app.

**The deployment shape.** Two stacks run the same image from different
branches on one host: production (`team-kb`, `main`, `:8000`) and staging
(`team-kb-dev`, `develop`, `:8001`), each with its own untracked
`.deploy/<stack>/deploy.env` and its own compose project — so each has its
own `workspacedata` volume.

## Goals / Non-Goals

**Goals:**

- One stack can run the archive pipeline while the other does not, from the
  same image, with no rebuild.
- A deployment that configures nothing behaves exactly as it does today.
- Enabling the pipeline stays an explicit act of a deployment.

**Non-Goals:**

- Changing the committed default to enabled.
- Changing how any other archive knob resolves.
- Building a general per-deployment config-override framework; this closes
  one specific gap.
- Deciding whether production should ever run the pipeline — that is a
  separate call, deliberately left open.
- Documenting or altering the archiving behaviour itself.

## Decisions

### D1 — Add the missing env override rather than flipping the committed default

Add `enabled` to `ArchiveSettings` and thread it through
`merge_archive_config` like every other knob.

- *Alternative: set `enabled: true` in `config/app.yaml`.* Rejected — the
  file is baked into the image, so this enables the pipeline in production at
  the next release as well. The feature executes unattended file moves once
  confidence clears the threshold, and has not yet run in any environment.
- *Alternative: carry enablement only in compose.* Rejected — compose has no
  opinion to express here; the value has to be resolvable by the application,
  and the deployment already has a channel (`deploy.env`) for exactly this.

### D2 — Tri-state resolution: `enabled: bool | None = None`, `None` defers to app.yaml

The field must default to `None`, not `False`. With `False` the environment
would win on every deployment that never sets the variable, which would
silently invert the behaviour of any deployment whose `app.yaml` enables the
pipeline. `None` preserves "the environment expressed no opinion" and keeps
today's resolution intact for every deployment that sets nothing.

- *Alternative: `bool = False` default.* Rejected for the reason above — it
  makes the override unconditional and changes existing behaviour.
- *Alternative: treat the value as a plain string and parse it.* Rejected —
  pydantic already parses `"true"`/`"false"` for a `bool | None` field, and
  matching the other knobs' typing keeps the merge function uniform.

### D3 — Pass the variable through the compose service's explicit environment list

Add `ARCHIVE_ENABLED:` beside the existing `ARCHIVE_*` passthroughs. The
compose `environment:` map is the only hop that reaches the container's
process environment; `--env-file` supplies substitution values but does not
inject anything the service does not list.

### D4 — Document the archive section, which is currently undocumented

`docs/config-reference.md` is the declared single home for `.env` / `app.yaml`
prose, and its `## config/app.yaml` section covers `engine` and `plugin` but
has no `archive` entry. The change adds it, together with the `ARCHIVE_*`
environment overrides, so the enable switch is discoverable rather than
inferred from a 503 message.

## Risks / Trade-offs

- **The override could be set without the container ever seeing it.** → Both
  hops are covered by the tasks: the settings field, the merge, and the
  compose passthrough. A settings-level test asserts the resolved value, and
  the compose entry is checked directly.
- **A future reader may "simplify" `bool | None = None` to `bool = False`.** →
  D2 records why the sentinel is load-bearing, and a test asserts that an
  unset environment defers to `app.yaml` in both directions.
- **Enabling the pipeline on staging acts on a shared volume.** → The staging
  stack is a separate compose project with its own `workspacedata` volume, so
  the trial cannot reach production's workspace.
- **The trial could behave unexpectedly on first run.** → Staging is the
  intended venue precisely because it is disposable; production stays off for
  the whole change, and rollback is removing one line from an untracked file.

## Migration Plan

1. Land the change on `develop`; the staging pipeline redeploys on its next
   poll. Production is untouched — the committed default stays off.
2. Operator step, not in the repo: add `ARCHIVE_ENABLED=true` to the staging
   stack's `.deploy/develop/deploy.env`, then let the pipeline redeploy (or
   restart that stack), and confirm the archive routes stop answering 503 in
   the webapp log.
3. Trial the pipeline on staging.

**Rollback:** set `ARCHIVE_ENABLED=false` (or remove the line) in
`.deploy/develop/deploy.env` and redeploy that stack. Production is
unaffected throughout, so no release-level rollback is involved.

## Open Questions

- Whether production should eventually enable the pipeline, and under what
  threshold. Deferrable: this change only makes the choice possible, and
  leaves the committed default off until that call is made deliberately.
- Whether a meaningful staging trial needs a seeded inbox with representative
  documents, and where those fixtures live. Deferrable to the trial itself;
  it does not change the design or the task breakdown.
