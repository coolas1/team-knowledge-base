## Why

Configuration is split across three systems that do not agree with each other,
so a knob can have two homes or none.

- `config/settings.py` (`InfraSettings`) reads `.env` through pydantic
  prefixes: `POSTGRES_*`, `LLM_*`, `ARCHIVE_*`, `HINDSIGHT_*`,
  `ENGINE_TOOLS_*`, `PI_AGENT_*`.
- `config/app.yaml`, validated by `config/schema.py`, holds the engine,
  ingest, memory, plugin and archive knobs. Only the `archive` section has
  environment overrides, through the hand-written `merge_archive_config`.
- Roughly fifty knobs under `app.yaml`'s `engine.memory`, `engine.ingest`,
  `engine` and `plugin` sections cannot be set from the environment at all —
  changing one means editing a committed file and rebuilding the image.

Two consequences, both already visible in the deployment:

1. A deployment cannot switch an ingest or memory knob without a rebuild,
   even though the archive section proved the need and established the pattern.
2. Memory tuning is described in two places at once — the `HINDSIGHT_*`
   environment variables and `app.yaml: engine.memory` — with no stated rule
   for which wins when they disagree.

`.env.example` has absorbed the cost: it has grown to 195 lines, mostly
restating defaults already present in code, so the handful of values a
deployment must genuinely choose are buried among the many it must not.

The manual cost is compounding. In recent revisions the `HINDSIGHT_*` family
alone gained 19 knobs, and each one had to be written by hand in three
separate places — a field in `config/settings.py`, a line in `.env.example`,
and a line in `docker-compose.yml`'s environment block. Nothing verifies the
three agree, so they can and eventually will drift.

## What Changes

**One override mechanism.** A new `config/overrides.py` derives an environment
name for every leaf path in the `AppConfig` schema — `TKB_` plus the path
joined by `_`, uppercased, so `engine.ingest.chunk_concurrency` becomes
`TKB_ENGINE_INGEST_CHUNK_CONCURRENCY`. Names are generated from the known
schema paths and tested for existence, never parsed back into a path, so
underscores inside key names (`chunk_concurrency`, `llm_retries`) create no
ambiguity. A test asserting the generated name set has no collisions keeps it
that way as the schema grows. The existing archive merge keeps its role — the
derived rule composes with it, so `ARCHIVE_*` still beats its derived
`TKB_ARCHIVE_*` equivalent, which in turn beats the committed default.

**Three named layers, one precedence.** Effective configuration resolves
`config/app.yaml` (committed defaults, never written by the running app) →
`.env` (per-deployment facts) → `config/app.runtime.yaml` (runtime UI edits).
`GET /api/config` returns the effective configuration with each key's source;
`PUT /api/config` writes the runtime layer instead of mutating the committed
defaults file.

**A smaller `.env`.** `.env.example` drops to the per-deployment facts a
deployment genuinely chooses — credentials, endpoints, host ports, paths and
feature switches — leaving behaviour defaults to `config/app.yaml`. Two tests
keep it honest: every key in the template is read somewhere, and every
deployment-required key is present.

**A reachability guard.** A test enumerates every leaf of the resolved schema
and asserts each has a reachable environment override, so a knob that cannot
be configured per deployment fails the suite instead of shipping silently.

## Explicitly out of scope

Recorded here so the boundary is not mistaken for an oversight:

- **Consolidating the duplicated knobs.** Folding `HINDSIGHT_*` and
  `ENGINE_TOOLS_*` into `app.yaml`'s `engine.memory` and `engine` sections is
  the bulk of the eventual work and follows as its own change, against a
  mechanism this change proves first.
- **The pi-agent sidecar.** Its ~50 `PI_AGENT_*` / `TKB_*` keys, with
  defaults hardcoded in `src/extensions/pi-agent/src/config.ts`, adopt the
  same convention in a second change.
- **Reaching the container.** The derived rule reads the process environment
  and the working directory's `.env`, which covers host runs (CLI, MCP,
  `uv run`) unchanged. The compose deployment is a further step: its
  `environment:` block is an explicit allowlist and `podman compose
  --env-file deploy.env` only feeds `${VAR}` interpolation, so a new `TKB_*`
  name does not enter the container until the deployment's env file is named
  `.env` and the services carry `env_file: .env`. That renames a file the
  pipeline, rollback and backup scripts read from the stable dir outside the
  clone, so it needs a host migration and a redeploy — a second change, with
  no code dependency on this one.
- **Durability of runtime overrides.** `config/` is baked into the image
  (`Containerfile:123` — `COPY config/ ./config/`) and `/app/config` is not a
  compose mount, so a runtime edit survives only until the next container
  recreation. The current `PUT /api/config` has this same defect today. The
  layer is named and ordered here so persistence can be added without
  changing precedence semantics.

## Capabilities

- **New capability `configuration`** — spec delta included. Defines the layer
  model and its precedence, the derived override rule, per-key source
  reporting, and the reachability guarantee.
- **No delta to `app-deployment`** — its existing "Auto-archiving enablement
  is injectable per deployment" requirement remains satisfied; `ARCHIVE_*`
  keeps working as a documented alias.

## Impact

- `config/overrides.py` (new) — schema walk, env-name derivation, collision
  guard.
- `config/schema.py` — gains the layered resolution (`load_config` becomes
  the defaults + runtime merge; env applied over it). The environment layer
  reads the working directory's `.env` as well as the process environment,
  the latter winning, matching pydantic-settings' `env > .env` order — so
  `.env` is the middle layer the design names, rather than a file only
  `InfraSettings` can see.
- `config/settings.py` — `ArchiveSettings` keeps `workspace_dir`; its other
  fields stop needing the tri-state `None` convention, because the shared
  mechanism detects an override by the variable's presence rather than by a
  sentinel value.
- `src/frontend/webapp/server/routes_config.py` — reads effective config with
  sources; writes `config/app.runtime.yaml`.
- `.gitignore` — ignores `config/app.runtime.yaml`.
- `.env.example` — rewritten down to per-deployment facts.
- `docs/config-reference.md` — documents the layer model, the derived naming
  rule, and which names are aliases.
- `tests/config/` — override derivation, collision guard, precedence order,
  reachability sweep, and `.env.example` honesty checks, joining the existing
  `test_settings.py`.

No database or SPA change: `AppConfig`'s schema is unchanged,
`GET /api/config` gains an additive `sources` field alongside the config it
already returns, and the SPA does not call `/api/config` at all.
