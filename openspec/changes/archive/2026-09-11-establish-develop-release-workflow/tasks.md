## 1. Single version source

- [x] 1.1 Create a repo-root `VERSION` file containing `0.2.0` and verify `cat VERSION` prints `0.2.0`
- [x] 1.2 Add a version helper in the BFF that reads `VERSION` at startup and defaults to `"unknown"` when the file is absent; verify a unit test covers both the present-file and missing-file cases
- [x] 1.3 Replace the hardcoded `version="0.1.0"` in `app.py` (FastAPI `version=...`) with the helper; verify `uv run pytest` passes and the app reports `0.2.0`
- [x] 1.4 Bump `pyproject.toml` `version` to `0.2.0` to match; verify `uv run pytest` still passes

## 2. Version endpoint and commit identifier

- [x] 2.1 Add `GET /version` returning `{"version": "<semver>", "commit": "<sha-or-null>"}` and exempt it from the SPA fallback route the same way `/health` is; verify `curl http://127.0.0.1:8000/version` returns both fields on a local run
- [x] 2.2 Read the commit from a `GIT_COMMIT` env var (empty/unset → `null`); verify a BFF test covers the set and unset cases

## 3. Version visible in the UI

- [x] 3.1 Add a minimal SPA footer component that fetches `/version` and renders `v0.2.0 (abc1234)`; verify `cd src/frontend/webapp/client && npm test` passes and the footer appears in the dev server

## 4. Commit injection into the build

- [x] 4.1 Add `ARG GIT_COMMIT=""` to the Containerfile and surface it to the running process as an env var; verify a build (`podman compose build`) succeeds and the var is present in the image's environment
- [x] 4.2 Add `COPY VERSION ./` to the Containerfile and confirm `.dockerignore` does not exclude it; verify the file exists inside a freshly built image
- [x] 4.3 Pass the build commit from `cicd/pipeline.sh` `stage_build` (the `SHORT_SHA` it already computes) as `GIT_COMMIT`; verify a `--dry-run` pipeline run completes the build with the arg wired

## 5. Workflow documentation

- [x] 5.1 Rewrite the workflow section of `CLAUDE.md`: develop (integration) + main (stable, deployed) branches, OpenSpec archive-after-merge performed by the maintainer per merge, and a maintainer-owned batched release; verify the doc reads as one coherent project rule
- [x] 5.2 Document the release runbook — merge `develop` → `main`, bump `VERSION` and `pyproject.toml` together, then tag `v0.x.y` — and the one-time bootstrap (fast-forward `develop`); verify the steps are complete and copy-paste runnable

## 6. Bootstrap and release (maintainer, post-apply)

> **Resolved (apply 2026-09-11).** `origin/develop` had diverged from
> `origin/main` with 24 commits of in-progress memory work, so a literal
> fast-forward would have discarded it. The memory work was preserved on a new
> branch `feat/memory-consolidation`, the old `develop` was deleted, and a
> fresh `develop` was created from `main` (i.e. the bootstrap is done, just not
> via a fast-forward of the old branch).

- [x] 6.1 Fast-forward `origin/develop` to `origin/main`; verify `git ls-remote origin develop` and `git ls-remote origin main` report the same head SHA
- [ ] 6.2 Release: merge `develop` → `main`, tag `v0.2.0` at the merged head; verify `git describe --tags` reports `v0.2.0` and the deployed instance's `/version` reports `0.2.0` plus the deploy SHA
