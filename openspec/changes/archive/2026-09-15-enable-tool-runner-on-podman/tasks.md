## 1. Gateway podman compatibility (src/extensions/tool-runner)

- [x] 1.1 Change the jobTemplate `/work` tmpfs options to `rw,noexec,nosuid,nodev,size=32m,mode=0777` (drop `uid=1000,gid=1000`) in `src/extensions/tool-runner/src/jobs.ts`, and extend the isolated-template unit test to assert the new options and that no `uid=`/`gid=` tmpfs parameter appears (podman 4.9 compat API rejects them).
- [x] 1.2 Add a private `remove(id)` helper to `Jobs` (best-effort `POST /containers/{id}/kill?signal=SIGKILL`, then `DELETE ?force=1`) and route both the per-job teardown `finally` and the stale-container sweep in `initialize()` through it; unit-test with a fake Docker that kill precedes delete, kill failure is ignored, and delete failure still propagates.
- [x] 1.3 Re-run the tool-runner unit suite (`cd src/extensions/tool-runner && npx vitest run`) — all existing and new tests pass without the integration flag.

## 2. Compose and pipeline wiring

- [x] 2.1 Parameterize the tool-runner volume in `docker-compose.yml` to `${TOOL_RUNNER_DOCKER_SOCKET:-/var/run/docker.sock}:/var/run/docker.sock` and verify `docker compose config` (default) still renders `/var/run/docker.sock` and renders the override when the variable is set.
- [x] 2.2 In `cicd/pipeline.sh`, extend the build stage to build with `--profile tool-images --profile tool-authoring` (so `tool-job` and `tool-runner` images build) and add `team-kb-tool-job` and `team-kb-tool-runner` to `compose_images`; verify with `bash -n` and a `--dry-run` pipeline execution that the build stage covers the four images.
- [x] 2.3 Verify (sandbox, no prod impact) that with `COMPOSE_PROFILES=tool-authoring`, `TOOL_RUNNER_DOCKER_SOCKET=/run/user/1001/podman/podman.sock`, and a generated token in a copied env file, `podman compose --env-file <copy> config --services` lists `tool-runner` with the socket volume — the deploy stage then needs no change.

## 3. Documentation and env template

- [x] 3.1 Add a podman deployment section to `src/extensions/tool-runner/README.md`: `TOOL_RUNNER_DOCKER_SOCKET=/run/user/<uid>/podman/podman.sock`, `COMPOSE_PROFILES=tool-authoring`, `systemctl --user enable podman.socket` for boot resilience, generated token ≥ 24 chars, `PI_AGENT_RUNNER_URL=http://tool-runner:8020`, and a note that the pipeline builds the tool images automatically.
- [x] 3.2 Add `TOOL_RUNNER_DOCKER_SOCKET` and `COMPOSE_PROFILES` entries to `.env.example` next to the existing `PI_AGENT_RUNNER_*` block.

## 4. Integration verification (sandbox against live podman socket)

- [x] 4.1 Build the `job` and `gateway` images from the patched source with podman and run `RUN_TOOL_RUNNER_INTEGRATION=1 RUNNER_DOCKER_SOCKET=/run/user/1001/podman/podman.sock RUNNER_JOB_IMAGE=team-kb-tool-job:latest npx vitest run` in a throwaway copy — all 12 tests pass, including busy-loop and process-flood cleanup within the request timeout.
- [x] 4.2 Run the built gateway image as a throwaway container with the podman socket mounted and a test token; verify `GET /health` (Bearer) reports `available:true` and a `POST /jobs` executes a real program end-to-end; clean up the container and any labeled job containers afterwards.

## 5. Repo gate

- [x] 5.1 Run `uv run ruff check` and `uv run pytest` from the repo root plus the SPA tests (`cd src/frontend/webapp/client && npm test`) — all green before pushing the branch.

## 6. LAN deployment (post-release, untracked local steps)

- [ ] 6.1 After the change reaches main and the pipeline deploys it: populate `.deploy/deploy.env` with `PI_AGENT_RUNNER_URL=http://tool-runner:8020`, a generated `PI_AGENT_RUNNER_TOKEN` (≥ 24 chars), `TOOL_RUNNER_DOCKER_SOCKET=/run/user/1001/podman/podman.sock`, `COMPOSE_PROFILES=tool-authoring`; remove the stopgap `PI_AGENT_TOOL_AUTHORING_ENABLED=false` block; run `systemctl --user enable podman.socket`; redeploy via `.deploy/run.sh --force`.
- [ ] 6.2 Verify on the LAN: `team-kb-tool-runner` container is running; pi-agent `GET /health` reports `toolAuthoring.enabled=true` and `runner.available=true` with capabilities; a real `execute_code` round-trip through a Pi session succeeds; `podman ps` shows no leftover `tkb.owner=team-kb-tool-runner` job containers after the run.
