## ADDED Requirements

### Requirement: Package indexes default to upstream
The image build SHALL resolve Python packages from the upstream public index by default. A regional mirror SHALL be applied only when explicitly requested through a build argument, and a default build MUST NOT depend on any mirror host's availability.

#### Scenario: Default build
- **WHEN** the image is built without mirror arguments
- **THEN** dependency resolution uses `pypi.org` and the build has no dependency on mirror hosts

#### Scenario: Mirror opt-in
- **WHEN** the build is invoked with the mirror build argument
- **THEN** dependency resolution uses the configured mirror
