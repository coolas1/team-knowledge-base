## ADDED Requirements

### Requirement: Deployment data is backed up
The pipeline SHALL back up the deployment's Postgres data and the uploads volume before each redeploy, storing backups in the stable directory and retaining a bounded history of the most recent backups. Backups SHALL be restorable to recover documents and their original files as of the backup time.

#### Scenario: Backup precedes redeploy
- **WHEN** the pipeline redeploys a new commit
- **THEN** a database dump and an uploads snapshot are written to the stable directory before the running stack is replaced

#### Scenario: Backup restores lost data
- **WHEN** an operator restores the latest backup after data loss
- **THEN** documents indexed before the backup and their original upload files are available again
