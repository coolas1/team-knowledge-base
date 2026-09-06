## Why

Image builds were dominated by avoidable work. The build context shipped ~1.88 GB because `.dockerignore` was a blocklist that missed `.git/`, the benchmark corpus, uploads, caches, and `node_modules/`. The uv toolchain was installed at build time via `curl` from astral.sh — ~78% of wall clock in the worst runs (one spent ~113 minutes on it, another failed with curl exit 35 through the proxy). And the layer order meant any code edit invalidated the Python dependency layer. A full rebuild took tens of minutes and failed often; iteration speed suffered.

## What Changes

- Switch `.dockerignore` from a blocklist to an **allowlist**: only the paths the Containerfile actually copies (`src/`, `config/`, `pyproject.toml`, `uv.lock`) enter the context. Everything else — VCS data, tests, benchmark corpora, uploads, caches — stays out. Context size drops from ~1.88 GB to ~4 MB.
- Replace the astral.sh curl installer for uv with a digest-pinned `COPY --from` of the official uv image, so the toolchain step performs no network fetch at build time (external image refs resolve from local storage, which is warm).
- Reorder Containerfile layers by change frequency: system deps → toolchain (Node 22, uv) → Python deps (only the lock inputs are copied, keeping `uv sync` cached across code edits) → SPA build (client source only) → app source. A Python-only edit now re-runs only the final `COPY` layers.
- Exclude `**/__pycache__`, `**/*.py[cod]`, and `**/*.egg-info` from the context so stale host bytecode (including residue from the pre-restructure layout) never enters the image or shadows source changes.

## Capabilities

### New Capabilities
- `container-build`: Properties of the container image build — a minimal allowlisted build context, a network-free pinned toolchain, and layer ordering that keeps dependency layers cached across source edits.

### Modified Capabilities

None.

## Impact

- `.dockerignore` — rewritten as an allowlist plus bytecode/output exclusions.
- `Containerfile` — uv via digest-pinned `COPY --from`; layer order restructured; comments record the evidence (context sizes, wall-clock breakdown, buildah failure modes).
- No runtime behavior, image entrypoint, or dependency changes; the built image is functionally identical.
- Retroactive note: this change documents work already applied to `main` (commits `c835264d`, `914c2fbf`, `1fcb8889`). The artifacts record what landed; they do not gate it.
