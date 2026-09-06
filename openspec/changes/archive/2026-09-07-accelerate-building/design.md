## Context

Builds run under podman/buildah on this host, behind a proxy, with the repo on cephfs. The measured costs: a 1.88 GB build context (blocklist `.dockerignore` missed `.git/`, `benchmark/`, uploads, caches), and a `curl -LsSf https://astral.sh/...` uv installer step that was 78% of wall clock in bad runs — one spent ~113 minutes on it, another failed with curl exit 35 through the proxy. A prior multi-stage build attempt (`COPY --from` an SPA build stage) failed under buildah on this storage backend ("io: read/write on closed pipe", "operation not permitted" on the inter-stage tar pipe), so the build is deliberately single-stage with Node 22 installed into the Python image. See proposal.md - Why for the motivation.

## Goals / Non-Goals

**Goals:**
- Make a no-change rebuild a cache hit and a source-only edit cheap (final layers only).
- Eliminate the flaky, dominant network step from the build.
- Fail closed on context contents.

**Non-Goals:**
- No multi-stage build (blocked by the buildah inter-stage copy bug documented in the Containerfile header).
- No changes to runtime image contents, entrypoint, or dependency versions (the PyPI mirror remap predates this change and is unchanged).
- No CI pipeline changes.

## Decisions

### Allowlist `.dockerignore` (`*` then `!src`, `!config`, `!pyproject.toml`, `!uv.lock`)
- Why: blocklists require predicting every future junk directory — `benchmark/` and `.cx/` arrived after the last blocklist edit, which is exactly how 1.88 GB happened. An allowlist needs a new rule only when the recipe gains a new `COPY`.
- Alternative: keep extending the blocklist — rejected as the documented failure mode.
- Re-adding `**/node_modules`, `**/dist`, `**/__pycache__`, `**/*.py[cod]`, `**/*.egg-info` after the allowlist is belt-and-braces: the negation rules guarantee these never come in even if a future allowlist line would otherwise admit them (e.g. `!src` admits `src/**/dist`).

### uv via digest-pinned `COPY --from=ghcr.io/astral-sh/uv:0.12.5@sha256:…`
- Why: an external image ref in `--from` resolves from local image storage (warm after one pull), so the toolchain step has no network dependency and no proxy exposure; the digest pin makes it bit-stable.
- Alternative: the astral.sh standalone installer (previous approach) — rejected: it was the single dominant failure/latency source.

### Layer order by change frequency
system deps → Node 22 → uv → lock inputs + `uv sync` → SPA (client source only) → `src/` + `config/`.
- Why: copying only `pyproject.toml` + `uv.lock` (not the whole source) keeps the expensive `uv sync` layer cached across every code edit; isolating the SPA layer to `src/frontend/webapp/client/` means Python edits never re-run `npm ci && build`; the final `COPY src/` overlay cannot clobber `dist/` because `**/dist` is excluded from the context.
- Alternative: copying all sources before dependency install (previous order) — rejected: every edit invalidated `uv sync`.

### Bytecode exclusions as a correctness measure, not just size
- Why: the repo had stale `__pycache__` residue from the pre-restructure layout (e.g. `src/plugin/` with no sources). Host bytecode in the context could shadow source-only changes in edge cases and bloat layers; excluding `**/__pycache__` / `*.py[cod]` / `*.egg-info` makes the image depend on source alone.

## Risks / Trade-offs

- [Allowlist fails closed: a future `COPY` of a not-yet-allowlisted path breaks the build] → that is the desired failure mode — the fix is a one-line allowlist entry next to the `COPY`.
- [Digest-pinned uv goes stale] → bumping is a deliberate, reviewable change (the pin is visible in the recipe).
- [Node 22 curl install remains in the toolchain layer] → it re-fetches only when `NODE_VERSION` changes; it is one cached layer, not per-build work. Moving it behind the digest-pinned uv pattern (image ref) is possible later.

## Migration Plan

Already applied on `main` (`c835264d`, `914c2fbf`, `1fcb8889`). No runtime impact; the next image rebuild picks everything up. Rollback = revert the three commits (context returns to blocklist behavior).

## Open Questions

None.
