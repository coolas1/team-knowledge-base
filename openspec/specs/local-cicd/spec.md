# local-cicd Specification

## Purpose
Automates the path from team-accepted code on the remote `main` branch to a
running, verified LAN deployment of the knowledge-base service, so the
deployed service always reflects reviewed work and a failed gate never
regresses the live service.

## Requirements

### Requirement: Watch the accepted branch
The pipeline SHALL poll the remote `main` branch at a fixed interval. When
the branch head is unchanged, the pipeline SHALL take no action. When a new
commit is observed, the pipeline SHALL run its full test-build-deploy flow
against exactly that commit.

#### Scenario: No new commit
- **WHEN** the polled branch head matches the last deployed commit
- **THEN** the pipeline exits without testing, building, or redeploying

#### Scenario: New commit on main
- **WHEN** the polled branch head differs from the last deployed commit
- **THEN** the pipeline syncs that commit and proceeds to the test gate

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
image with the commit's SHA alongside a rolling tag, and redeploy the full
stack from the repository's compose definition using local deployment
credentials. Images SHALL be fully built before the running stack is
replaced, so downtime is bounded by container restart.

#### Scenario: New accepted commit deploys
- **WHEN** the gate passes for a new commit
- **THEN** the LAN service is redeployed on that commit and its health endpoint reports healthy

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
The pipeline SHALL record which commit SHAs have been deployed and keep the
image built for each recorded SHA available locally. Any previously deployed
SHALL be redeployable as a rollback path.

#### Scenario: Rollback to previous version
- **WHEN** a deployed version proves bad and an operator requests the previously deployed SHA
- **THEN** that SHA's image is redeployed and the service serves that version

### Requirement: Deployment health verification
After redeploying, the pipeline SHALL verify the service through its health
endpoint and SHALL report the deployment as failed if the service does not
become healthy within a bounded time.

#### Scenario: Unhealthy deploy is reported
- **WHEN** the health endpoint does not report healthy within the bound after a deploy
- **THEN** the pipeline reports a failed deployment instead of reporting success

### Requirement: Single operator of the deployment
The deployed stack SHALL be managed exclusively by the pipeline. Deploying,
updating, and tearing down the stack MUST go through the pipeline. The
development checkout MAY access deployed services only through their
published ports.

#### Scenario: Pipeline converges a drifted stack
- **WHEN** the pipeline deploys while containers from an earlier manual deployment still exist
- **THEN** the stack converges to the repository's compose definition, orphaned containers are removed, and named data volumes are preserved

#### Scenario: Development checkout stays a client
- **WHEN** a developer working in the development checkout needs the deployed Postgres, Neo4j, or webapp
- **THEN** they reach them via the published ports without invoking the deployment lifecycle

### Requirement: Credentials isolated from the source checkout
Deployment credentials and pipeline state SHALL be maintained in a stable
directory that lives outside the pipeline's disposable source clone, so
recreating or wiping the clone MUST NOT lose credentials, deploy history, or
deployed data. The stable directory SHALL be excluded from version control
(gitignored) when it lives inside a development checkout.

#### Scenario: Source checkout recreated
- **WHEN** the pipeline's disposable source clone is deleted and re-cloned
- **THEN** the next deployment uses the same credentials and data volumes with no manual reconfiguration

#### Scenario: Stable directory is untracked
- **WHEN** the stable directory lives inside the development repo's working tree
- **THEN** its contents (credentials, state, clone) never appear in git status or any commit
