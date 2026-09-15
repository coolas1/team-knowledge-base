# Proposal: formalize-repo

## Why

Ten changes have just been archived and the repo carries accumulated
documentation drift: the user-facing README still describes a refactored-away
directory layout (`src/tkb/*`) and a stale clone URL; `docs/` mixes user
manuals with one-time dev scratch notes; `.env.example` and `config/app.yaml`
carry hundreds of lines of embedded explanations; there is no PR template;
and `openspec/config.yaml` is still the commented-out scaffold. Formalizing
these now, on a clean tree, makes the repo legible for the collaborators the
develop/release workflow was built for.

## What Changes

- Add `.github/PULL_REQUEST_TEMPLATE.md` — a Chinese, ≤500-character PR
  description template (change summary, linked openspec change, test
  evidence).
- Rewrite `README.md` in Chinese — concise, user-facing; fix stale paths
  (`src/tkb/client`, `src/tkb/agent` → current layout), fix the clone URL
  (`Cried1` → `coolas1`), document the published LAN endpoints, and link to
  `docs/` as the manual appendix.
- Update `CLAUDE.md` (English, stays English) — add `src/extensions/`
  (tool-runner, pi-agent) to the Architecture section, reference the PR
  template and the new `docs/` layout.
- Refactor `docs/` into a user-manual appendix of the README — keep
  start/architecture/ark-image-ppt/deep-search-operations (plus a new
  config-reference page absorbing the `.env.example` explanations); move dev
  documents into `openspec/changes/archive/` or delete scratch
  (issues.md, todos.md, `superpowers/`); final per-file disposition is
  decided in design.
- Slim `.env.example` and `config/app.yaml` comments to one-line hints that
  point at the config-reference doc; keep every variable and default
  byte-identical, move only prose.
- Fill in `openspec/config.yaml` — `context` (stack, conventions,
  develop/release + archive-after-merge workflow) and `rules` (proposal and
  tasks conventions).

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

(none — documentation and tooling only; `.openspec.yaml` sets
`skip_specs: true`)

## Impact

- No runtime code changes; no config values change (prose moves, defaults
  stay identical).
- Files touched: `.github/PULL_REQUEST_TEMPLATE.md` (new), `README.md`,
  `CLAUDE.md`, `docs/**`, `.env.example`, `config/app.yaml` (comments only),
  `openspec/config.yaml`.
- Cross-reference sweep required wherever a moved/deleted doc is referenced
  (e.g. `.env.example` pointed at `docs/memory-scope.md`; check code
  comments and specs too).
- Validation: `uv run ruff check` and `uv run pytest` must stay green
  (they also guard against tests reading the moved docs); SPA tests
  unaffected.
