## Why

The repo currently runs on a single `main` branch: every change — proposal, code, and archive — lands directly on `main`, and the LAN pipeline redeploys `main` every five minutes. There is no integration branch, no release gate, and the app version is hardcoded as `0.1.0` in three separate places. As the team grows to multiple collaborators, changes need a place to integrate and be reviewed before they reach the stable deploy target, and the deployed app must report exactly which version it is running.

## What Changes

- **Split the remote into two branches.** `origin/main` becomes the stable, versioned branch the pipeline deploys; `origin/develop` becomes the integration branch every collaborator branches from.
- **Bootstrap the branches.** Fast-forward `origin/develop` to `origin/main` so both start from the same head, and set the semantic version to `v0.2.0` (re-tag from the current head).
- **Adopt the OpenSpec archive-after-merge convention.** A change maps to a feature branch off `develop`; its PR carries the spec delta plus the code; the maintainer archives the change (fold delta into `specs/`, move to `archive/`) by direct push to `develop` *after* the merge.
- **Introduce a maintainer-owned batched release.** The maintainer merges `develop` → `main`, bumps the version, and tags it. Release is a gate held by the maintainer, never by the PR author.
- **Introduce runtime version reporting.** The app exposes its semantic version and build commit through a `/version` endpoint and in the UI, sourced from a single version definition (`pyproject.toml`, set to `0.2.0`) instead of hardcoded strings.
- **Document the workflow** as the project rule in `CLAUDE.md`.

**Explicitly deferred to the next change:** dual-deployment CI/CD (running `develop` and `main` as two stacks under separate ports). This change keeps the pipeline single-stack and watching `main` only.

## Capabilities

### New Capabilities

- `versioning`: the running app reports its semantic version and build commit at runtime, derived from a single version source rather than hardcoded literals.

### Modified Capabilities

<!-- None. local-cicd and app-deployment behavior are unchanged: the pipeline
     still deploys main only (dual-deploy is deferred), and the workflow
     (branching/archive/release) is process, documented in CLAUDE.md, not
     system behavior. -->

## Impact

- **Docs / process:** `CLAUDE.md` (new branching + release workflow), `CLAUDE.local.md` (openspec workflow note).
- **Versioning:** `pyproject.toml` (`version` 0.1.0 → 0.2.0), `src/frontend/webapp/server/app.py` (read version from package metadata, add `/version` endpoint), SPA client (render version), `Containerfile` (accept `GIT_COMMIT` build arg), `cicd/pipeline.sh` (pass commit SHA into the build).
- **Git bootstrap:** fast-forward `origin/develop`, re-tag `v0.2.0` from the current head (performed at release, not as part of applying the code).
