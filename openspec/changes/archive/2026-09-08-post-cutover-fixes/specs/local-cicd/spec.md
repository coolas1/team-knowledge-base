## MODIFIED Requirements

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
