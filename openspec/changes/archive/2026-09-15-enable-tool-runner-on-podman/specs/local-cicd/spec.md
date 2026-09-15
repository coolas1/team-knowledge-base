## MODIFIED Requirements

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
