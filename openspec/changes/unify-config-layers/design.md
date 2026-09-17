## Context

Three systems configure the app today, with no stated relationship between
them:

| System | Home | Reachable from env |
|---|---|---|
| Infra/deployment facts | `config/settings.py` (`InfraSettings`) | yes, by construction |
| Engine/ingest/memory/plugin/archive behaviour | `config/app.yaml` + `config/schema.py` | only `archive`, by hand |
| Sidecar runtime | `src/extensions/pi-agent/src/config.ts` | yes, ~50 keys |

The archive section already implements the wanted pattern —
committed default, environment override, no rebuild — in
`src/engine/components/archive/config.py`. It needed a `None`-tri-state on
every overridable field, a hand-written merge function, and a comment warning
future readers not to "simplify" the tri-state to a plain default. Extending
that approach to the remaining ~50 knobs would mean ~50 more tri-states and a
much larger merge function. This change replaces the approach instead of
repeating it.

## Goals / Non-Goals

**Goals**

- Every knob in `config/app.yaml` is settable per deployment without editing
  a committed file or rebuilding an image.
- One stated precedence order, observable from the outside.
- `.env` shrinks to the facts a deployment actually chooses.
- Nothing becomes configurable and then silently unreachable again.

**Non-Goals**

- Consolidating `HINDSIGHT_*` / `ENGINE_TOOLS_*` with `app.yaml`'s overlapping
  sections.
- Bringing the pi-agent sidecar's configuration under the same scheme.
- Making runtime overrides survive container recreation (see below).

## The layer model

```
config/app.yaml              committed defaults — the full set, never written
        ↓ overridden by         by the running app
.env  (deploy.env)           per-deployment facts
        ↓ overridden by
config/app.runtime.yaml      runtime edits (PUT /api/config)
```

Resolution is one function over the three layers, highest wins per key.
Absent layers are simply absent — a deployment with no runtime file and no
overrides resolves exactly to the committed defaults.

The environment layer reads the working directory's `.env` in addition to
the process environment, with the process environment winning — the same
order pydantic-settings uses for `InfraSettings`. Without that, `.env` would
be the middle layer in name only: on a host run its `TKB_*` names would be
invisible to `load_config`, and only `InfraSettings` would ever see the file.

### Why the runtime layer is a separate file

`config/app.yaml` is currently both the committed default set and the file
`PUT /api/config` mutates. That makes "a runtime edit overrides the
environment" impossible to express: an edit to the same file that supplies
defaults cannot outrank a higher layer. Splitting the mutable layer into its
own file makes the ordering explicit and keeps the committed defaults
immutable at runtime — the running app never dirties a tracked file.

### Durability is deliberately deferred

`config/` is copied into the image (`Containerfile:123`) and `/app/config` is
not a compose mount — the webapp's volumes are `artifactsdata`,
`uploadsdata`, `workspacedata` and the HuggingFace cache. A write to the
runtime file therefore lands in the container's writable layer and is
discarded at the next recreation.

This is a pre-existing defect, not one introduced here: today's
`PUT /api/config` writes `config/app.yaml` into that same discarded layer.
It is also currently unexercised — the staging container's
`/app/config/app.yaml` is byte-identical to the repository, and the SPA
contains no reference to `/api/config` at all.

The layer is named and ordered by this change so that adding a mount later
changes only where the file lives, not the precedence semantics. Until then
the limitation is documented rather than implied.

## The derived override rule

For each leaf path in the `AppConfig` schema, the override name is:

```
TKB_ + "_".join(path_parts).upper()
```

so `engine.ingest.chunk_concurrency` → `TKB_ENGINE_INGEST_CHUNK_CONCURRENCY`
and `archive.collision_policy` → `TKB_ARCHIVE_COLLISION_POLICY`.

**Why this is unambiguous.** Names are only ever *generated* from paths that
already exist in the schema, and then tested for presence in the environment.
No environment name is ever parsed back into a path. Underscores inside key
names (`chunk_concurrency`, `entity_resolution_max_concurrent`) therefore
cannot be confused with path separators — the ambiguity that would plague a
parsing approach simply never arises. A test asserts the generated set is
collision-free, which is the only way two paths could share a name.

Verified against the names already in use: nothing currently matches
`TKB_ENGINE_*`, `TKB_PLUGIN_*` or `TKB_ARCHIVE_*`.

Coercion: the schema's pydantic types do the work. A value that fails
validation raises the same error it would from the file, naming the key.

## The two-layer split, stated plainly

This change makes the division explicit rather than changing what lives where:

- **Per-deployment facts** — credentials, endpoints, host ports, filesystem
  paths, and the feature switches a given deployment chooses — stay
  environment-native. They differ per deployment by nature, and several are
  secrets that must not sit in a committed file.
- **Behaviour defaults** — engine, ingest, memory, plugin and archive tuning —
  live in `config/app.yaml` as the readable full default set, and gain the
  derived override so a deployment can still change any of them.

## Existing prefixed names remain honoured

`ARCHIVE_*` is the one prefixed family that names keys in the committed
configuration: its nine names map onto `app.yaml: archive.*`. It is documented
and present in both live deployment env files (production :8000, staging
:8001), so renaming it in this change would silently drop live settings on a
production deployment the moment the file changed but the env file did not.

So: the derived `TKB_*` name is canonical for new keys, and `ARCHIVE_*` keeps
working as a documented alias for its archive key. Where both are set for the
same knob the explicit `ARCHIVE_*` name wins, preserving today's behaviour
exactly. Retiring the alias is a later change with its own migration.

`merge_archive_config` is deliberately left in place rather than folded into
the shared mechanism. It already resolves `ARCHIVE_*` correctly, it is covered
by tests, and the archive pipeline is switched on in a live deployment —
rewriting it would mean reworking those tests to gain no observable
behaviour. Composition yields the same precedence without the churn: the
derived rule resolves `TKB_ARCHIVE_*` into the `AppConfig` that
`merge_archive_config` then receives, and its tri-state `None` — meaning "the
environment did not speak" — defers to that value whenever `ARCHIVE_*` is
unset. The resulting order is `ARCHIVE_*` → `TKB_ARCHIVE_*` → `app.yaml`,
which is exactly what the alias rule requires.

`ARCHIVE_WORKSPACE_DIR` has no counterpart in `app.yaml` — it is a deployment
path, deliberately environment-only — and stays exactly as it is.

`HINDSIGHT_*` and `ENGINE_TOOLS_*` are a different case and are **not**
aliases. They are `InfraSettings` fields that happen to describe settings
`app.yaml` also describes under other names (`hindsight_graph_worker_enabled`
against `memory.graph_worker`). They are read from the environment natively
today and are unaffected by this change; reconciling them with their
`app.yaml` counterparts is the deferred consolidation work. They gain no
derived equivalent here, because there is no `app.yaml` key for one to alias.

## Reachability guard

A test walks the resolved `AppConfig` schema, collects every leaf path,
derives its override name, and asserts the derivation succeeds and the name
is unique. This is what prevents the class of defect this change exists to
fix: a knob that is reviewed, documented as configurable, and then turns out
to be reachable only by editing committed code.

The guard asserts *derivability*, not that the key is set — every knob has a
reachable override, whether or not any deployment uses it.

## Source reporting

`GET /api/config` returns the effective configuration plus, per key, which
layer supplied the value: `default`, `app.yaml`, `env`, or `runtime`. This
makes the precedence visible instead of a trap — a UI edit that loses to an
environment value can say so rather than appearing to succeed.

## Alternatives considered

**Tri-state `None` per overridable field** (generalize `merge_archive_config`
as-is). Rejected: ~50 tri-states, a correspondingly large hand-written merge
function, and a correctness argument that depends on every future field
remembering the convention.

**Move all defaults into Python and delete `app.yaml`.** Rejected: it puts
~60 tuning knobs into `.env`, which is the opposite of shrinking it, and
loses the single readable file that answers "what are this app's defaults?".

**Persist runtime overrides in Postgres.** Rejected for now: `load_config()`
is called from contexts without database access (`src/agent/tkb/mcp/server.py`,
`src/engine/hindsight_components/service.py`), so config resolution would
acquire a database dependency it currently does not have.

## Testing

- Derivation: representative paths map to expected names; the generated set is
  collision-free.
- Precedence: each layer overrides the one below, and an absent layer changes
  nothing (defaults plus env with no runtime file equals today's behaviour).
- Aliases: `ARCHIVE_*` still overrides `app.yaml`, and a prefixed name beats
  its derived equivalent when both are set.
- Reachability: every leaf path in the schema derives a name.
- `.env.example`: every key in the template is read by some consumer, and
  every deployment-required key appears.
- Route: `PUT /api/config` writes the runtime file and leaves `config/app.yaml`
  byte-identical; `GET /api/config` reports a source per key.

## Migration

No deployment action is required. With no `config/app.runtime.yaml` present
and no new environment names set, the resolved configuration is identical to
today's. Both live deployment env files keep working unchanged, because the
prefixed names they use remain honoured.
