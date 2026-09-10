# Outstanding work

Living to-do list. Last updated 2026-09-10, at the close of the
`fix-qa-and-pr-followups` change (local `6d899180`, deployed to the LAN stack
the same day).

## Where that change stands

Landed and verified in CI (`ruff`, `pytest` 349 passed / 5 skipped, SPA 33
passed, pi-agent 92 passed) and, for most items, against the live LAN
deployment. The pipeline deployed `6d89918` and wrote a pre-deploy backup
(`.deploy/backups/6d89918-20260910T035956Z/`: `postgres.sql.gz` 4.5 MB +
`uploads.tar.gz` 890 KB).

Verified live: malformed doc ID → 422 (incl. version routes), well-formed
miss → 404, oversized upload → 413, `GET /mcp` → JSON 404, junk
conversation entities gone from the graph, `hops` honored (3 nodes at 1 hop
vs 21 at 2) with populated links, `related_docs` no longer render `()`.

## Open — needs a code change

- [ ] **Conversation chunks still reach `/api/search`; `relation_type` is
      always empty.** The filters added to `src/engine/graphrag/_search.py`
      are never called on this deployment: `config/app.yaml` sets
      `engine.memory.enabled: true`, so `deps.py` wires the Hindsight query
      service and `routes_search` takes the `HindsightRecallAdapter` branch
      (which never calls `kb.recall()` → `full_search()`). The adapter also
      rebuilds `related_docs` from recalled sources, so no real relation type
      is ever produced.
      Fix: filter conversation-sourced chunks in
      `src/engine/hindsight_components/compat.py` (and stop emitting
      conversation docs as `related_docs`), with tests — so both search paths
      honor the public/private split. This is why tasks 3.4/3.6 are only
      half-effective; their unit tests exercise the unwired path.
- [ ] **Entity names split at spaces** (`红曲霉（Monascus` /
      `purpureus）` are separate graph nodes). Needs its own change with an
      eval; deliberately out of scope for the above.
- [ ] **Artifacts have no retention policy** (PR #4 P4): generated files
      accumulate in the `artifactsdata` volume. Add a TTL / max-size sweep.
- [ ] **Repo-level CI is still missing.** The LAN pipeline gates per deploy,
      but nothing runs lint+tests on PRs.
- [ ] **Ungated `EXISTS` subquery** in `_recall_source_conditions`
      (`src/engine/hindsight_components/repository.py`) runs on every recall
      arm even with conversation memory disabled. Negligible cost; gate it
      when convenient.

## Open — operations / verification

- [ ] **8.3 data remediation is blocked on facts.** The 2026-09-09 web QA
      report (since retired to git history — see commit `6d899180`) describes
      38 docs with 10 failed images and 27 stale 09-03 rows, but the live
      corpus on 2026-09-10 was **44 docs / 43 indexed / 1 failed**. Nothing
      matches the described resubmit/delete targets, so no data was touched.
      Next: identify the intended rows (the new backup makes this safe to
      inspect) and confirm before any delete.
- [ ] **8.4 15-question bench eval not run** (harness skipped this session).
      Re-run after the search-path fix to confirm the stricter deep-mode
      gates keep answer quality (mean ≥ 0.9, vs 0.93 on 2026-09-03).
- [ ] **Write-path live checks not done** (they change the shared corpus, so
      they need explicit authorization): a versioned edit end-to-end (new
      version indexed, old retired) and a bulk re-upload to prove the new
      retry/backoff self-heals — that last one is the only real proof the
      PR #4 P2 ingest fix works under load.
- [ ] **SPA 404 page unverified in a browser** — the fallback serves the
      shell (200 HTML) by design; the catch-all 404 is client-side and has no
      DOM test (the SPA suite runs `environment: 'node'`).

## Open — environment / infra

- [ ] **Fix git push credentials.** `git push` fails: the credential helper
      points at a VSCode-server `askpass.sh` that no longer exists. Work
      around with `gh auth login` or a working credential helper. (The user
      pushed `6d899180` manually.)
- [ ] **Two untracked directories at the repo root duplicate existing code**
      and have never been examined: `configs/` (mirrors `config/`) and
      `src/engine/memory/` (22 files mirroring
      `src/engine/hindsight_components/`). Created 2026-09-10 ~10:23 by
      something other than this session. Decide: delete, or adopt/merge.
- [ ] **Node versions differ by surface.** Project standard is **20.20.2**
      (`/var/tmp/node-v20.20.2-linux-x64`, on `PATH` via `~/.bashrc`); the
      pipeline gate uses **22.19.0**; `pi-agent`/`tool-runner` need **≥24**
      (`/var/tmp/node-v24.21.0-linux-x64`, used to run that suite). Worth
      pinning in one place if this keeps biting.

## Doc hygiene

- [ ] `docs/issues.md` is a 2026-09-03 snapshot; its "carried over" section
      is now landed, and N1–N10 are largely addressed or superseded. Refresh
      or retire it.
- [x] Retired `docs/qa-report-2026-09-09-webapp.md` and
      `docs/upstream-pr-followups.md` on 2026-09-10. Both are recoverable
      from git history (`git show 6d899180:docs/<name>`); open items from the
      ledger now live in this file.
- Note: the QA report's corpus counts (38 docs / 10 failed images) never
  matched the live deployment — see 8.3. Treat that report's *bug list* as
  the durable part, not its numbers.
