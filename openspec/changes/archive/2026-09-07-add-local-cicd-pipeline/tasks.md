## 1. MCP host-trust fix

- [x] 1.1 Add `webapp:8000` to `allowed_hosts` in `src/agent/tkb/mcp/server.py` (keep existing entries) and add/extend a unit test asserting the transport security settings accept localhost and all deployment-internal service names; verify with `uv run pytest` on the touched test module
- [x] 1.2 Add a test asserting an unknown Host header remains rejected (DNS-rebind protection stays active); verify it fails only on an unrelated host value, not on `webapp:8000`

## 2. Pipeline script (`cicd/pipeline.sh`)

- [x] 2.1 Implement watch + sync stages: compare `git ls-remote origin main` against the recorded last-deployed SHA, and on change `git fetch` + `reset --hard origin/main` in the disposable clone; verify two consecutive runs no-op on an unchanged head
- [x] 2.2 Implement the gate: `ruff check`, `uv run pytest` with `UV_PROJECT_ENVIRONMENT=/var/tmp/tkb-venvs/cicd`, SPA `npm test` with `/var/tmp/node22/bin` first on PATH; verify a deliberately failing suite aborts the run before any build and the running stack is untouched
- [x] 2.3 Implement build + deploy: `podman compose build`, tag built images with the short SHA alongside `:latest`, then `podman compose up -d --remove-orphans --env-file /var/tmp/team-kb-cicd/deploy.env`; verify containers are recreated while named volumes keep their creation date and data
- [x] 2.4 Implement post-deploy verification: poll `GET /health` with a bounded timeout and exit non-zero on failure; verify the failure is visible in `systemctl --user status` / journal
- [x] 2.5 Implement rollback helper: document/execute redeploy of a prior `:<sha>` image; verify a rollback restores the previous version's health response

## 3. Bootstrap, units, and installation

- [x] 3.1 Write the static bootstrap `/var/tmp/team-kb-cicd/run.sh` that execs `repo/cicd/pipeline.sh`, tracked as a template under `cicd/`; verify it runs the pipeline from a clean stable dir
- [x] 3.2 Write systemd user service + timer templates under `cicd/` (5-min cadence, no overlapping runs) and install them into `~/.config/systemd/user`; verify with `systemctl --user list-timers` and `loginctl show-user` reporting `Linger=yes`
- [x] 3.3 Seed `/var/tmp/team-kb-cicd/deploy.env` from the `.env` the live stack currently uses; verify `podman compose --env-file` renders the intended configuration (`podman compose config` shows resolved values, no leaked secrets in output)

## 4. Cutover and end-to-end verification

- [x] 4.1 Dry-run sync + gate + build against current `origin/main` with deploy withheld; verify all gate stages pass on a clean clone and images build
- [x] 4.2 Perform the cutover deploy (`up -d --remove-orphans`): confirm `team-kb-pi-agent-host` is removed as an orphan, named volumes carry over (a pre-cutover ingested document stays searchable), `/health` is green, and a pi-agent MCP round-trip succeeds (proves in-network host trust); also confirm nothing on the LAN depended on pi-agent at `:8010` directly
- [x] 4.3 Enable the timer and verify the full loop end-to-end: after the next merge to `origin/main`, the journal shows poll → gate → build → deploy → healthy with no manual action

## 5. Documentation

- [x] 5.1 Add `cicd/README.md`: install steps, cutover runbook, rollback command, ownership rule (pipeline is the sole compose operator; dev checkout is a client via published ports); verify the runbook matches the actual paths and commands used in tasks 3–4
- [x] 5.2 Update the root `CLAUDE.md` workflow section with the deployment flow and the single-operator rule; verify commands referenced there exist
