## ADDED Requirements

### Requirement: Auto-archiving enablement is injectable per deployment

The auto-archiving pipeline's enable switch SHALL be resolvable from the
deployment environment, and an environment value SHALL take precedence over
the committed application configuration. The committed configuration SHALL
leave the pipeline disabled, so that running it is always an explicit act of
a single deployment rather than an inherited default. Turning the pipeline on
or off for one deployment SHALL NOT require editing committed configuration
or rebuilding the image.

#### Scenario: A deployment opts in

- **WHEN** a deployment enables the archive pipeline through its environment
  while the committed configuration leaves it disabled
- **THEN** that deployment runs the archive scanner and worker, and other
  deployments running the same image do not

#### Scenario: Defaults are unchanged

- **WHEN** a deployment sets no archive enablement value
- **THEN** the pipeline resolves exactly as it does today, which is disabled

#### Scenario: A deployment overrides a committed enablement

- **WHEN** a deployment disables the archive pipeline through its environment
  while the committed configuration enables it
- **THEN** the pipeline does not run in that deployment

#### Scenario: Enablement takes effect without a rebuild

- **WHEN** an operator enables or disables the pipeline for one stack
- **THEN** the change is effective the next time that stack starts, with no
  change to committed configuration and no image rebuild
