## Context

The app ships as one backend process (BFF + engine + plugin) running from source. The container builds with `uv sync --frozen --no-dev --no-install-project` (Containerfile line 69), so the project is **never installed as a distribution** in the image — only its dependencies are. This rules out `importlib.metadata.version()` as a runtime version source. The pipeline (`cicd/pipeline.sh`) already computes the source commit (`HEAD_SHA`/`SHORT_SHA`) at build time. Version is currently hardcoded as `0.1.0` in `pyproject.toml` and `src/frontend/webapp/server/app.py`. See proposal.md for motivation.

## Goals / Non-Goals

**Goals:**
- One place defines the app's semantic version; the running backend reads it instead of hardcoding.
- A `/version` endpoint and a UI footer report `semver` + `commit` for any running instance.
- A two-branch (develop + main) workflow with maintainer-owned release, documented as the project rule.

**Non-Goals:**
- Dual-deployment CI/CD (develop + main as two live stacks) — deferred to the next change.
- Changing `local-cicd` pipeline behavior: it keeps watching and deploying `main` only.
- Touching the unrelated `version` fields in `src/agent/interface.py` and `src/extensions/pi-agent/.../mcp-client.ts` (plugin-manifest and MCP-protocol versions, not app semver).

## Decisions

### D1 — Runtime version source is a repo-root `VERSION` file
A plain-text `VERSION` file (`0.2.0`) is the canonical version. The BFF reads it at startup through a small helper (default `"unknown"` if absent).

- **Chosen over `importlib.metadata.version()`** because the container runs from source without installing the project (see Context).
- **Chosen over parsing `pyproject.toml` at runtime** because `pyproject.toml` is a build input, not guaranteed present in the runtime image, and reading TOML adds a parser dependency.
- **Chosen over a generated `_version.py`** because there is no build hook to generate it under `--no-install-project`.

`pyproject.toml`'s `version` field is bumped to match at release as packaging metadata; the release runbook updates `VERSION` and `pyproject.toml` together in the same commit.

### D2 — Commit identifier flows in as a `GIT_COMMIT` build arg
The Containerfile accepts `ARG GIT_COMMIT=""`, exposes it to the process as an env var (default empty), and `/version` reads it. `cicd/pipeline.sh` `stage_build` passes the `SHORT_SHA` it already computes. Empty/unset → the endpoint reports the commit as unknown.

- **Chosen over baking the SHA into a file at build** because a build arg is the idiomatic container mechanism and needs no extra files in the allowlisted context.

### D3 — `/version` endpoint beside `/health`
`GET /version` returns `{"version": "0.2.0", "commit": "abc1234"}` (commit `null` or empty when unknown). It lives next to `/health` in the BFF and is exempted from the SPA fallback route the same way `/health` already is (see `app.py`).

### D4 — UI renders the version from `/version`
The SPA fetches `/version` on load and renders `v0.2.0 (abc1234)` in a footer. A colocated, minimal component; no new state library.

### D5 — Workflow is documentation, not spec
The branching/release process (develop + main, archive-after-merge by the maintainer, maintainer-owned batched release) is captured in `CLAUDE.md` as the project rule. It is team process, not system behavior, so it does not get a spec delta. The only spec-level change is `versioning`.

### D6 — Bootstrap and release ordering
1. Fast-forward `origin/develop` to `origin/main` (one-time; both land on the current head).
2. This change proceeds on a feature branch off `develop` → PR → merge → maintainer archives (per-merge).
3. Release: the maintainer merges `develop` → `main`, and tags `v0.2.0` at the resulting head (`VERSION` is already `0.2.0` from the apply).

## Risks / Trade-offs

- **`VERSION` and `pyproject.toml` version can drift** → the release runbook updates both in one commit; the runtime reads only `VERSION`, so a drift affects packaging metadata, not the reported version.
- **`VERSION` file must be copied into the image** → add one `COPY VERSION ./` line to the Containerfile and confirm `.dockerignore` does not exclude it.
- **Local dev runs lack a commit hash** → `/version` degrades gracefully to `commit: null`; the spec's "commit unknown" scenario covers this.
- **Fast-forwarding `develop` is a destructive remote op** → do it once, before collaborators branch from `develop`, and only when `develop` has no commits beyond `main`.

## Migration Plan

1. Apply the code (VERSION file, helper, `/version` endpoint, SPA footer, `GIT_COMMIT` build arg + pipeline injection, CLAUDE.md rewrite).
2. Verify locally: `uv run pytest`, SPA `npm test`, and `GET /version` on a local run.
3. Bootstrap: fast-forward `origin/develop` to `origin/main`.
4. Follow the new flow to merge this change to `develop`, archive it, then release (merge → `main`, tag `v0.2.0`).

No rollback is required beyond the existing `cicd/rollback.sh`: the running stack is untouched until the release merge lands on `main`.
