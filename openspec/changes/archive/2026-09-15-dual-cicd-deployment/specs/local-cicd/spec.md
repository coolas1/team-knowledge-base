## MODIFIED Requirements

### Requirement: Watch the accepted branch
The pipeline SHALL run as one independent instance per tracked branch: a
production instance watching the remote `main` branch and a staging instance
watching the remote `develop` branch. Each instance SHALL poll its branch at
a fixed interval. When the branch head is unchanged, the instance SHALL take
no action. When a new commit is observed, the instance SHALL run its full
test-build-deploy flow against exactly that commit, independent of the other
instance's state.

#### Scenario: No new commit
- **WHEN** the polled branch head matches the last deployed commit of that
  instance
- **THEN** the instance exits without testing, building, or redeploying

#### Scenario: New commit on develop
- **WHEN** the remote `develop` head differs from the staging instance's last
  deployed commit
- **THEN** the staging instance syncs that commit and proceeds to its test
  gate, without regard to the production instance's state

#### Scenario: New commit on main
- **WHEN** the remote `main` head differs from the production instance's last
  deployed commit
- **THEN** the production instance syncs that commit and proceeds to its test
  gate

### Requirement: Deployed-version history and rollback
Each pipeline instance SHALL record which commit SHAs have been deployed to
its stack and keep the image built for each recorded SHA available locally.
Any previously deployed SHA SHALL be redeployable as a rollback path for that
stack. A rollback on one stack MUST NOT alter the containers, images,
volumes, or deployed version of the other stack.

#### Scenario: Rollback to previous version
- **WHEN** a deployed version proves bad and an operator requests the
  previously deployed SHA
- **THEN** that SHA's image is redeployed and the stack serves that version

#### Scenario: Staging rollback leaves production serving
- **WHEN** the staging stack is rolled back to an earlier develop commit
- **THEN** the production stack keeps serving its deployed version with its
  containers and volumes untouched

### Requirement: Single operator of the deployment
Each deployment stack SHALL be managed exclusively by its pipeline instance.
Deploying, updating, and tearing down a stack MUST go through that stack's
pipeline. The development checkout MAY access either deployed stack only
through its published ports.

#### Scenario: Pipeline converges a drifted stack
- **WHEN** a pipeline instance deploys while containers from an earlier
  manual deployment of its stack still exist
- **THEN** that stack converges to the repository's compose definition,
  orphaned containers are removed, and named data volumes are preserved

#### Scenario: Development checkout stays a client
- **WHEN** a developer working in the development checkout needs a deployed
  stack's Postgres, Neo4j, or webapp
- **THEN** they reach them via that stack's published ports without invoking
  the deployment lifecycle

### Requirement: Credentials isolated from the source checkout
Each pipeline instance's deployment credentials and pipeline state SHALL be
maintained in its own stable directory that lives outside the pipeline's
disposable source clone, so recreating or wiping a clone MUST NOT lose
credentials, deploy history, or deployed data. The stable directories of the
two instances SHALL be siblings excluded from version control (gitignored)
when they live inside a development checkout.

#### Scenario: Source checkout recreated
- **WHEN** a pipeline's disposable source clone is deleted and re-cloned
- **THEN** the next deployment uses the same credentials and data volumes
  with no manual reconfiguration

#### Scenario: Stable directory is untracked
- **WHEN** either instance's stable directory lives inside the development
  repo's working tree
- **THEN** its contents (credentials, state, clone) never appear in git
  status or any commit

## ADDED Requirements

### Requirement: Deployment stacks are isolated
The production and staging stacks SHALL occupy distinct namespaces — compose
project, container names, image names, volumes, networks — and distinct host
ports, so a deployment action on one stack MUST NOT create, recreate, remove,
or rename any container, image, or volume of the other stack. The shared
compose definition SHALL derive every namespace-bearing name from a
per-deployment environment value.

#### Scenario: Staging deploy leaves production running
- **WHEN** the staging pipeline deploys a new develop commit
- **THEN** the production stack's containers keep running under their
  existing names and the production health endpoint stays healthy throughout

#### Scenario: Namespaces do not collide
- **WHEN** both stacks are deployed from the same compose definition
- **THEN** every container, image, and volume of one stack carries a name
  distinct from the other's, and neither stack's redeploy affects the other's
  objects

#### Scenario: Distinct published ports
- **WHEN** both stacks serve concurrently on one host
- **THEN** each stack publishes its API and backing services on host ports
  that do not overlap the other stack's

### Requirement: Staging stack starts with empty data
The staging stack SHALL start with empty data stores: it MUST NOT be seeded
with production documents, upload files, agent sessions, or graph data, and
its data stores MUST remain separate from production's.

#### Scenario: First staging deploy is empty
- **WHEN** the staging stack is deployed for the first time
- **THEN** its document list is empty and no production data is present,
  while the production stack's data is unchanged

