## 1. Context allowlist

- [x] 1.1 Rewrite `.dockerignore` as an allowlist (`*` + `!src`, `!config`, `!pyproject.toml`, `!uv.lock`); verify the assembled context drops from ~1.88 GB to ~4 MB and contains only the recipe's copy inputs (`c835264d`).

## 2. Toolchain and layer ordering

- [x] 2.1 Replace the astral.sh curl installer with a digest-pinned `COPY --from` of the official uv image; verify the toolchain layer builds with no build-time fetch of install scripts and the pinned digest is recorded in the recipe (`914c2fbf`).
- [x] 2.2 Reorder layers by change frequency (system deps → Node 22 → uv → lock inputs + `uv sync` → SPA from client source only → `src/` + `config/`); verify a Python-only edit rebuild hits cache for dependency and SPA layers and re-runs only the final copies, and an SPA-only edit keeps the `uv sync` layer cached (`914c2fbf`).

## 3. Context hygiene

- [x] 3.3 Exclude `**/__pycache__`, `**/*.py[cod]`, `**/*.egg-info` from the context; verify no host bytecode enters the image and the final source overlay cannot clobber the built SPA output (`**/dist` also excluded) (`1fcb8889`).

## 4. Verification

- [x] 4.1 Rebuild the image via the compose webapp service and verify the app boots and serves as before (functional equivalence of the image); record the build wall-clock improvement noted in the recipe comments (uv installer was 78% of wall clock in bad runs).
- [x] 4.2 Validate the change with `openspec validate accelerate-building --strict` and confirm the three landed commits touch only `.dockerignore` and `Containerfile`.
