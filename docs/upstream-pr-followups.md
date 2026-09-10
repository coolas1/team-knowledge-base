# Upstream PR follow-ups (PR #3 & PR #4)

Outstanding issues from the reviews of PR #3 (recall relevance threshold
gate) and PR #4 (conversation memory + document workflows). Both PRs were
accepted as-is, merged upstream, and re-mapped onto the three-module layout
(merge `f979e858`, 2026-09-03). Items are checked off as they land; the
2026-09-10 `fix-qa-and-pr-followups` change landed most of them. Paths use
the current layout.

## PR #3 — recall relevance threshold gate

- [x] **P1 — Deep-mode gate logic contradicts its own documentation.**
  Fixed by dropping the `elif` in `_filter_by_relevance`
  (`src/engine/hindsight_components/recall.py`): the semantic floor now
  applies in every mode and the deep-mode score gate applies on top (the
  documented both-gates semantics).
- [x] **P2 — BFF happy-path test was repurposed instead of extended.**
  `tests/frontend/test_bff_agent.py` now has a happy-path test (the Acme
  fixture's score raised above the gate, asserting the LLM answer) plus a
  separate below-gate test asserting the shared not-found constant.
- [x] **P3 — Not-found string hardcoded in three places.**
  Extracted to `NOT_FOUND_ANSWER` in `src/engine/interface.py`, used by
  `reflect.py` and both skills.
- [ ] **P4 — Containerfile bakes the Aliyun PyPI mirror into the shared
  build.** `sed`-remapping `uv.lock` URLs to `mirrors.aliyun.com` and the
  pinned astral.sh uv installer (0.12.5) make every environment's image
  build depend on those hosts. Make the mirror opt-in (build arg /
  deployment overlay) instead of unconditional.

## PR #4 — conversation memory + document workflows

- [x] **P1 — No transcript size bound on retention (design promised one).**
  `enqueue_conversation_turn` now truncates combined turn content at
  `conversation_max_turn_chars` (default 100,000) with a `[truncated]`
  marker.
- [x] **P2 — Fire-and-forget reindex tasks can die silently.**
  Covered by the shared background-task registry + done-callback (see the
  PR #6 N2 item below, the same umbrella fix).
- [x] **P3 — `generate_document` blocks the event loop.**
  `generate_artifact` now runs in `asyncio.to_thread`
  (`src/agent/tkb/mcp/server.py`).
- [ ] **P4 — Artifacts have no retention policy.**
  Generated files accumulate forever in the `artifactsdata` volume. Add a
  TTL or max-size sweep (e.g. delete artifacts older than N days on
  worker poll). _Explicitly out of scope for the 2026-09-10 change._
- [x] **P5 — Silent failure swallowing where the design promises
  diagnostics.** pi-agent now logs every swallowed recall/retention
  failure, and reports an unreachable status as `unavailable` instead of
  fabricating `failed: 1`.
- [x] **P6 — Minor cleanups.**
  - `_conversation_operation_failed` now includes the underlying error
    (`"{type}: {msg}"`).
  - `formatConversationMemoryBlock` no longer emits an unclosed
    `<untrusted_conversation_memory>` tag when the budget cannot fit
    opening + one line + closing.
  - Upload cap added at the BFF (`KB_MAX_UPLOAD_BYTES`, default 100 MiB,
    413 `file_too_large`) — the frontend's 413 handling is now reachable.
  - `_recall_source_conditions`'s `EXISTS` subquery is still ungated when
    conversation memory is disabled (negligible; not tasked).

## Cross-PR

- [x] **Conversation recall is subject to PR #3's relevance gates.**
  Conversation-memory recall now uses its own lower floor
  (`conversation_recall_min_semantic`, default 0.25) instead of the public
  0.45 gate; the memory-recall test no longer relies on a keyword hit.

## PR #6 — versioned documents

Follow-ups from the PR #6 re-review (author wwwttlll), merged as `b1bfd43a`
on 2026-09-09; both landed in the 2026-09-10 change.

- [x] **N1 — Duplicate `logger` assignment survived the review fix.**
  Deleted the duplicate `logger = logging.getLogger(__name__)` in
  `src/engine/graphrag/backend.py`.
- [x] **N2 — `edit_document` demotes the old version before a
  fire-and-forget reindex.**
  The bare `create_task` sites (`edit_document` plus both `reingest`
  branches) now go through a module-level task registry with a
  done-callback that logs and marks the affected row — the **new version
  row** for a versioned edit — `failed` with a non-empty error;
  cancellation is not reported as failure. `_ingest_one` keeps its
  returns-the-task invariant (documented in a comment), so those sites
  need no registry. The detail page's pending-poll surfaces the failure,
  pinned by a component test.

## Repo-level

- [ ] **No CI on the repository.** Both PRs' "tests pass" claims were
  author-reported only. Add at least `uv run ruff check` +
  `uv run pytest` on PRs (unit/contract/BFF tests pass without live
  services), plus the two npm suites for touched paths. _The local LAN
  pipeline (`cicd/`) gates on lint + tests per deploy, but there is still
  no PR-level CI._

## Manual appended issue by me
- cited from a session: "the spec scenario promises original files stay "downloadable", but the BFF has no document-download endpoint (I verified presence-on-volume + re-ingest instead) — a small candidate for a future change if browser download of originals matters."
