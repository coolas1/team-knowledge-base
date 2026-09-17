## ADDED Requirements

### Requirement: Configuration resolves in three ordered layers

The system SHALL resolve effective configuration from three layers, each
overriding the one below it per key: the committed defaults in
`config/app.yaml`, then environment overrides, then runtime edits held in
`config/app.runtime.yaml`. A layer that is absent SHALL contribute nothing, so
that a deployment with no runtime file and no environment overrides resolves
exactly to the committed defaults.

#### Scenario: Environment overrides the committed default

- **WHEN** a key is set in `config/app.yaml` and a different value for the
  same key is supplied through the environment
- **THEN** the environment value is effective

#### Scenario: Runtime edits override the environment

- **WHEN** a key is supplied through the environment and the same key is
  written through the config API
- **THEN** the runtime value is effective

#### Scenario: Absent layers change nothing

- **WHEN** no runtime file exists and no environment override is set
- **THEN** the effective configuration equals the committed defaults

### Requirement: Every settings key has a derived environment override

Every leaf key in the application configuration schema SHALL have a reachable
environment override, derived from its path by upper-casing the path segments
joined with underscores and prefixing `TKB_`, so that `engine.ingest.chunk_concurrency`
is overridden by `TKB_ENGINE_INGEST_CHUNK_CONCURRENCY`. Derivation SHALL NOT
require any per-key code, so a newly added settings key is overridable without
further work. Changing any settings key for one deployment SHALL NOT require
editing a committed file or rebuilding the image.

#### Scenario: A previously unreachable key becomes settable

- **WHEN** a deployment sets `TKB_ENGINE_MEMORY_CONSOLIDATION_BATCH_SIZE`
  while `config/app.yaml` holds a different value
- **THEN** the configured value is effective for that deployment without
  editing `config/app.yaml`

#### Scenario: A new settings key is overridable by construction

- **WHEN** a settings key is added to the schema
- **THEN** its derived environment name overrides it with no additional
  mapping code

### Requirement: Derived override names are collision-free

The set of derived override names SHALL NOT contain duplicates, so two
distinct settings paths can never be configured by the same environment
variable. A collision SHALL fail the test suite rather than silently
resolving one key to the other's value.

#### Scenario: A collision is rejected

- **WHEN** two schema paths would derive the same environment name
- **THEN** the check fails and names both paths

### Requirement: Existing prefixed environment names remain honoured

`ARCHIVE_*` — the one set of environment names that already overrides keys in
the committed configuration — SHALL continue to override its corresponding
`archive` keys, and SHALL remain documented. Where both a prefixed name and
its derived equivalent are set for the same key, the prefixed name SHALL win,
preserving the behaviour of deployments that set it. Path settings that exist
only in the environment SHALL be unaffected.

#### Scenario: A live deployment env file keeps working

- **WHEN** a deployment that sets `ARCHIVE_ENABLED` runs against a revision
  carrying the derived rule
- **THEN** `ARCHIVE_ENABLED` still controls the archive pipeline switch

#### Scenario: Prefixed name takes precedence

- **WHEN** both `ARCHIVE_ENABLED` and `TKB_ARCHIVE_ENABLED` are set to
  different values
- **THEN** `ARCHIVE_ENABLED` is effective

#### Scenario: Environment-only path settings are unaffected

- **WHEN** a deployment sets a path that is deliberately environment-only,
  such as `ARCHIVE_WORKSPACE_DIR`
- **THEN** it continues to resolve from the environment as before

### Requirement: The effective configuration reports each key's source

The config API SHALL report, for each key, which layer supplied its effective
value — the committed default, `config/app.yaml`, the environment, or the
runtime layer — so that a value that is not in effect can be distinguished
from one that is.

#### Scenario: An environment-controlled key is identifiable

- **WHEN** a key's value comes from the environment and a caller reads the
  effective configuration
- **THEN** that key is reported as supplied by the environment

### Requirement: Runtime edits never mutate the committed defaults

Writing configuration through the config API SHALL modify only the runtime
layer. The committed `config/app.yaml` SHALL NOT be written by the running
application, so a runtime edit cannot change the defaults other deployments
inherit from the same image.

#### Scenario: A committed file survives a runtime edit

- **WHEN** configuration is written through the config API
- **THEN** `config/app.yaml` is unchanged byte for byte and the written value
  is held in the runtime layer

### Requirement: The environment template lists only per-deployment facts

The committed `.env.example` SHALL list the values a deployment genuinely
chooses — credentials, endpoints, host ports, filesystem paths and feature
switches — and SHALL NOT restate behaviour defaults that the committed
configuration already supplies. Every key it lists SHALL be read by some
consumer, and every value a deployment cannot run without SHALL be present.

#### Scenario: Template contains no dead entries

- **WHEN** a key is listed in `.env.example` that no consumer reads
- **THEN** the check fails and names that key

#### Scenario: Required deployment values are present

- **WHEN** a value required for a deployment to start is missing from the
  template
- **THEN** the check fails and names the missing key
