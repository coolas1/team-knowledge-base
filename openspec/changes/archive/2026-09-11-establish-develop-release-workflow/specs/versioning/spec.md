## Purpose

The running application reports which release it is — its semantic version and the exact source commit it was built from — so an operator can always identify what is deployed.

## ADDED Requirements

### Requirement: Single source of truth for the version
The application's semantic version SHALL be defined in exactly one place, and the running backend SHALL read its version from that source rather than from a hardcoded literal in application code.

#### Scenario: Version bump propagates
- **WHEN** the single version definition is changed
- **THEN** the backend reports the new version without any edit to application code

### Requirement: Version endpoint
The backend SHALL expose a `/version` endpoint that reports the running application's semantic version and the commit identifier the running build was produced from.

#### Scenario: Version reported
- **WHEN** a client requests `/version`
- **THEN** the response reports the current semantic version and the build commit

#### Scenario: Commit unknown
- **WHEN** the running build was produced without a commit identifier (for example, a local development run)
- **THEN** the endpoint still reports the semantic version and marks the commit as unknown rather than failing

### Requirement: Deployed version is identifiable
The pipeline SHALL provide the source commit's identifier to the image build, so the commit reported by a deployed instance's `/version` endpoint matches the commit the pipeline deployed.

#### Scenario: Deployed commit matches the source
- **WHEN** the pipeline builds and deploys commit X
- **THEN** the deployed instance reports commit X through `/version`

### Requirement: Version visible in the UI
The SPA SHALL display the running semantic version and commit, obtained from the version endpoint, so a user can see which release they are using.

#### Scenario: User sees the running version
- **WHEN** a user opens the SPA
- **THEN** the running semantic version and commit are visible in the interface
