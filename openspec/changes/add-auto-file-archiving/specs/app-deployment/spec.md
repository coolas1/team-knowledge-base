## ADDED Requirements

### Requirement: The workspace and uploads are durable volumes
The compose deployment SHALL mount a persistent workspace volume into the
webapp container containing the archive inbox, the archive directory tree, and
the uploads directory, so original files survive container replacement by the
CI pipeline. Archive-related configuration (thresholds, band width, poll
interval, mode) SHALL be injectable via environment variables sourced from
`.env`, consistent with the existing configuration flow.

#### Scenario: Files survive a redeploy
- **WHEN** the CI pipeline redeploys the webapp container
- **THEN** files in the inbox, the archive tree, and uploads remain on disk
  and `documents.file_path` references remain valid

#### Scenario: Configuration is external
- **WHEN** the deployment sets archive thresholds or mode via `.env`
- **THEN** the running backend applies them without image changes
