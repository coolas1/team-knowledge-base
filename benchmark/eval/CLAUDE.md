# eval/ -- benchmark harness: corpus map + QA + file-update suites

## Module summary

The benchmark harness. `STORY.md` is the canonical map of `raw/` (file inventory,
cross-file relations, suggested queries); `qa/` and `file-update/` are the two
scored suites the apps under test are measured against. Agents under test must NOT
read `STORY.md` or the answer keys - those are the scoring ground truth.

Direct children:

- `STORY.md` - canonical corpus map: inventory, relations, suggested queries. Also
  `STORY_ja.md` / `STORY_zh.md` (JA/ZH mirrors). **Forbidden to eval agents.**
- `qa/` - 40-question multi-modal retrieval suite: `questions/NN-q.md` +
  `answers/NN-a.md`, plus `PREDICT_INSTRUCTIONS.md` / `EVAL_INSTRUCTIONS.md`
  runbooks and a `README.md` (format). Cross-lingual (JA/ZH) items included.
- `file-update/` - 20-entry file-edit + knowledge-update benchmark. Each
  `entries/NN-*/` has `golden/`, `verify-a/` (Agent A edit prompt), `verify-q/`
  (Agent B verify prompt). Agent A edits; Agent B verifies; automated eval.

## Hard-won knowledge

<!-- Inclusion rule: add an entry only if it is non-obvious, repo-related, and
     painful to re-derive. Each entry: the decision (1-3 sentences) + why.
     Truly long-form decisions link out to a doc instead of bloating this file. -->

- **STORY.md is off-limits to eval agents.** It holds the answers (relations,
  suggested queries). raw/ files must never link to it either - it is a
  design/ground-truth doc, not corpus content.
- **Markdown in raw/ carries no frontmatter.** Titles, types, dates, tags, and the
  `links:` graph live in `STORY.md`, not the files - so the suites score against
  prose-only retrieval. Agents reading frontmatter get nothing.
- **Multi-modal files are leaf nodes.** PDF/XLSX/CSV/PNG/JPG under raw/ aren't
  parsed as text (only `.md` is); their contents are described by surrounding
  markdown, so QA items about them test cross-modal prose retrieval.
- **40 QA + 20 file-update entries are the validity count.** A complete eval tree
  has 40 paired Q/A files in `qa/` and 20 entries in `file-update/entries/`.
