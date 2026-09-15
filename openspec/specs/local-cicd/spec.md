# local-cicd Specification

## Purpose
Automates the path from team-accepted code on the remote `main` branch to a
running, verified LAN deployment of the knowledge-base service, so the
deployed service always reflects reviewed work and a failed gate never
regresses the live service.

## Requirements

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

### Requirement: Test gate before deployment
The pipeline SHALL gate deployment on the repository's static checks and
unit-level test suites (Python lint, Python unit/contract/BFF tests, and
the SPA client test suite). If any gate step fails, deployment MUST NOT
proceed and the currently running service MUST remain untouched and serving.

#### Scenario: Tests fail
- **WHEN** any gate step fails for the new commit
- **THEN** no new image is deployed and the previously deployed service continues to serve requests

#### Scenario: Tests pass
- **WHEN** all gate steps pass for the new commit
- **THEN** the pipeline proceeds to build and deploy that commit

### Requirement: Build and deploy as a LAN service
The pipeline SHALL build the service images from the gated commit, tag each
built image with the commit's SHA alongside a rolling tag, and redeploy the
full stack from the repository's compose definition using local deployment
credentials. Images SHALL be fully built before the running stack is
replaced, so downtime is bounded by container restart. The build SHALL
include the images of services gated behind compose profiles that the
deployment has enabled, and the deployed stack SHALL include those opt-in
profile services, so a redeploy neither skips building them nor removes a
running opt-in service as an orphan.

#### Scenario: New accepted commit deploys
- **WHEN** the gate passes for a new commit
- **THEN** the LAN service is redeployed on that commit and its health endpoint reports healthy

#### Scenario: Tool-authoring profile enabled
- **WHEN** the deployment's credentials file enables the `tool-authoring` compose profile and the gate passes for a new commit
- **THEN** the pipeline builds the tool-runner and tool-job images, tags them with the commit's SHA, deploys the tool-runner service alongside the stack, and a subsequent redeploy leaves the tool-runner running

### Requirement: Deployed data persists across deployments
The pipeline MUST preserve the deployment's named data volumes (vector
store, graph store, uploaded document files, artifacts, agent data) across
deployments. Redeployment MUST NOT delete, reinitialize, or
migrate-destructively any data volume.

#### Scenario: Documents survive a redeploy
- **WHEN** the service is redeployed on a new commit
- **THEN** documents ingested before the redeploy remain searchable afterwards

#### Scenario: Original files survive a redeploy
- **WHEN** the service is redeployed on a new commit
- **THEN** files uploaded before the redeploy remain present on the uploads volume, and their documents remain downloadable, editable, and re-ingestable

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

### Requirement: Deployment health verification
After redeploying, the pipeline SHALL verify the service through its health
endpoint and SHALL report the deployment as failed if the service does not
become healthy within a bounded time.

#### Scenario: Unhealthy deploy is reported
- **WHEN** the health endpoint does not report healthy within the bound after a deploy
- **THEN** the pipeline reports a failed deployment instead of reporting success

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

### Requirement: Deployment data is backed up
The pipeline SHALL back up the deployment's Postgres data and the uploads volume before each redeploy, storing backups in the stable directory and retaining a bounded history of the most recent backups. Backups SHALL be restorable to recover documents and their original files as of the backup time.

#### Scenario: Backup precedes redeploy
- **WHEN** the pipeline redeploys a new commit
- **THEN** a database dump and an uploads snapshot are written to the stable directory before the running stack is replaced

#### Scenario: Backup restores lost data
- **WHEN** an operator restores the latest backup after data loss
- **THEN** documents indexed before the backup and their original upload files are available again

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
