## 1. Config reference doc (absorbs prose before files are slimmed)

- [x] 1.1 Create `docs/config-reference.md` (Chinese) covering every variable in `.env.example` and every section of `config/app.yaml`, organized by concern (数据库 / 模型端点 / 检索 / 记忆 / Pi Agent / tool-runner / 构建); port the existing comment prose verbatim-in-meaning — verify every variable name in `.env.example` appears in the doc.
- [x] 1.2 Verify `docker-compose.yml` `${VAR}` substitutions and `src/*/config/settings.py` env names all appear in the reference (no unexplained knob left behind).

## 2. Slim `.env.example` and `config/app.yaml`

- [x] 2.1 Rewrite `.env.example`: identical variables/defaults/ordering, one-line hints only, top-of-file pointer to `docs/config-reference.md`; verify `diff <(grep -E '^[A-Z_]+=' old) <(grep -E '^[A-Z_]+=' new)` is empty and `docker compose config` renders identically.
- [x] 2.2 Slim `config/app.yaml` comments the same way; verify `uv run pytest` passes (settings load unchanged).

## 3. docs/ refactor (design D3 table)

- [x] 3.1 Move the six dev documents into their matching `openspec/changes/archive/2026-09-15-*/docs/` directories per the D3 table; verify `git status` shows pure renames.
- [x] 3.2 Delete `docs/issues.md`, `docs/todos.md`, `docs/superpowers/`; verify nothing tracked references them (`grep -r "docs/todos\|docs/issues\|superpowers" --include="*.md" --include="*.py" --include="*.ts" .`).
- [x] 3.3 Cross-reference sweep (D6): grep for every moved path (`docs/memory-scope.md` etc.) across the repo and repoint/drop each hit; verify the grep returns zero stale paths.
- [x] 3.4 Update `docs/start.md` to dedupe against the new README and point at `docs/config-reference.md` for configuration detail; verify its commands run as written (paths exist).

## 4. README + CLAUDE.md

- [x] 4.1 Rewrite `README.md` in Chinese per D2 structure with verified paths (`src/frontend/webapp/client`, `src/extensions/...`, `coolas1` remote, LAN ports 8000/8001); verify every command and path mentioned exists in the repo.
- [x] 4.2 Update `CLAUDE.md`: add `src/extensions/` (tool-runner, pi-agent) to Architecture, reference `.github/PULL_REQUEST_TEMPLATE.md` and the new `docs/` layout; keep English; verify no stale references remain.

## 5. PR template

- [x] 5.1 Create `.github/PULL_REQUEST_TEMPLATE.md` (Chinese, ≤500 chars) per D1: 变更说明 / openspec 变更 / 测试证据 / 目标分支提醒; verify the file is ≤500 characters (`wc -m`).

## 6. openspec/config.yaml

- [x] 6.1 Fill in `context` (stack, module map, commit scopes, develop/release + archive-after-merge workflow) and `rules` for `proposal` and `tasks` per D5; verify `openspec doctor` still reports ok and the YAML parses.

## 7. Validation & PR

- [x] 7.1 Run `uv run ruff check` and `uv run pytest` from the repo root and `cd src/frontend/webapp/client && npm test` — all green before pushing the branch.
- [ ] 7.2 Open the PR to `develop` using the new template (first dogfood); after merge, confirm the staging pipeline deploys the docs-only change cleanly and production is untouched.
