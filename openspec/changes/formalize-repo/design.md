# Design: formalize-repo

## Context

The repo now has a clean openspec state (all merged changes archived
2026-09-15) and a settled two-pipeline deployment (production :8000 on
`main`, staging :8001 on `develop`). Documentation has not kept up: README
still shows the pre-refactor `src/tkb/*` layout and the `Cried1` clone URL;
`docs/` holds 8 dev documents alongside 4 user documents plus scratch;
`.env.example` (221 lines, ~60% comments) and `config/app.yaml` (47 lines)
embed their own reference manual. See proposal.md — Why.

## Goals / Non-Goals

**Goals:**

- A Chinese, concise, accurate README that a new LAN user can follow
  end-to-end, with `docs/` as its manual appendix.
- One place for configuration prose (a Chinese config reference doc); env
  and yaml files carry only one-line hints.
- A PR template that makes the "spec delta + code" PR convention visible at
  PR-creation time.
- `openspec/config.yaml` that tells artifact-writing agents the real
  workflow.

**Non-Goals:**

- No translation of existing English manuals (`architecture.md`,
  `deep-search-operations.md` stay English; can be translated later).
- No content changes to moved dev docs — they are preserved verbatim as
  archive supplements.
- No changes to any config default or variable set.
- No openspec spec deltas (skip_specs: true).

## Decisions

**D1 — PR template: `.github/PULL_REQUEST_TEMPLATE.md`, Chinese, ≤500 chars.**
Body: 变更说明 (what/why), openspec 变更 (change name or "docs-only, no
change"), 测试证据 (lint/test results), plus a one-line reminder that PRs
target `develop` and carry the spec delta. Alternative: a longer English
template — rejected: the collaborators are Chinese-speaking and the 500-char
cap keeps descriptions scannable; detail lives in the change artifacts.

**D2 — README (Chinese) structure:** 简介 → 功能 → 快速开始 (本地开发:
uv sync / .env / docker compose) → 访问部署 (LAN 生产 :8000、预发 :8001,
以客户端身份使用,不要手工 compose up) → 使用手册 (links into `docs/`) →
参与贡献 (branch off develop, PR template, lint+test before push, link
CLAUDE.md for full conventions). Keep to roughly one screen; anything longer
belongs in `docs/`.

**D3 — docs/ disposition, per file:**

| File | Disposition |
|---|---|
| `start.md` | Keep (快速启动, Chinese) — dedupe against new README, keep the detailed steps |
| `architecture.md` | Keep (English overview appendix) |
| `ark-image-ppt.md` | Keep (Chinese ops manual) |
| `deep-search-operations.md` | Keep (English ops manual) |
| `config-reference.md` | **New** — Chinese; absorbs all prose from `.env.example` + `config/app.yaml` |
| `versioned-documents-design.md` | Move into archive of the matching change as supplemental material |
| `hindsight-capability-matrix.md` | Move → `archive/2026-09-15-align-hindsight-memory-capabilities/docs/` |
| `session-transcript-validation.md` | Move → `archive/2026-09-15-preserve-session-transcript-history/docs/` |
| `file-memory-migration-2026-09-11.md` | Move → `archive/2026-09-15-reduce-memory-cost-and-rebuild-file-observations/docs/` |
| `memory-cost-controls.md` | Move → same archive dir as above |
| `memory-scope.md` | Move → `archive/2026-09-15-align-hindsight-memory-capabilities/docs/` |
| `issues.md`, `todos.md` | Delete (stale scratch; superseded by openspec) |
| `superpowers/` (plans, specs) | Delete (agent-workflow scratch, regenerable) |

Rationale: dev documents are historical design/validation records — the
matching archived change is their natural home; scratch notes have no
re-use value. Alternative: a `docs/dev/` graveyard — rejected, it recreates
the mixed-audience problem one level down.

**D4 — .env.example / config/app.yaml slimming:** every variable, default,
and ordering stays byte-identical; only comment prose moves to
`docs/config-reference.md`. Each section keeps a one-line hint plus
`# 详见 docs/config-reference.md`. Commented-out optional variables
(e.g. `# PI_AGENT_CONTEXT_WINDOW=...`) stay commented-out but lose their
multi-line explanations.

**D5 — openspec/config.yaml:** fill `context` (English — artifact language)
with the stack, module map, conventional-commit scopes, and the
develop/release + archive-after-merge workflow; fill `rules` for `proposal`
(concise, capabilities section must reflect skip_specs honestly) and
`tasks` (checkbox granularity, validation task last). Keep the file
minimal — it is prompt context, not a second CLAUDE.md.

**D6 — Cross-reference sweep:** grep the repo for every moved/deleted path
(`docs/memory-scope.md`, `docs/todos.md`, ...) and repoint or drop each
reference (known: `.env.example` → memory-scope; cicd/README.md and
src/extensions/tool-runner/README.md may reference docs). README rewrite
must use verified current paths (`src/frontend/webapp/client`,
`src/extensions/...`, `coolas1` remote).

## Risks / Trade-offs

- [A moved doc is referenced from code/tests and a link rots] → grep sweep
  is an explicit task (D6); `uv run pytest` must stay green.
- [Slimmed .env.example loses discoverability of a knob] → the config
  reference is linked from the top of `.env.example`, README, and
  `config/app.yaml`; nothing is deleted, only relocated.
- [Chinese README drifts from English CLAUDE.md over time] → accepted;
  README states facts only (paths, ports, commands) which change rarely,
  and CLAUDE.md remains the canonical dev doc.
- [Staging pipeline deploys this docs-only PR] → harmless by design; the
  gate (lint+tests) exercises the same checks we run locally.

## Migration Plan

Single PR to `develop`; no deploy-time migration. Rollback = revert the
merge commit. Archive after merge per the standard runbook (this change is
skip_specs, so archive is a pure move).

## Open Questions

- None blocking. (Translation of the two English manuals is a possible
  follow-up change, explicitly out of scope.)
