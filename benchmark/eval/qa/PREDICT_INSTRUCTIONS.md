# Prediction runbook — `<knowledge-base>` retrieval benchmark

> **Argument.** `<knowledge-base>` is the system under test. **Default `gbrain`.**
> Replace `<knowledge-base>` (and `<knowledge-base>-files`) everywhere below with
> the app you are evaluating. Future apps reuse this runbook unchanged.

## What this benchmark measures

This is a test of the **knowledge base itself** — how completely it ingested the
`raw/` corpus and how accurately it retrieves from it — *not* a test of your
general world knowledge. The corpus is a fixed, read-only "Wren Adachi" personal
archive (Markdown, PDF, Excel, CSV, PNG, JPG). Every answer lives in there; your
job is to pull it out **through the KB**.

## Core principles (read these first)

1. **Answer only from what the KB retrieves.** Do not fill gaps from your own
   training knowledge, even if a plausible answer is obvious to you. An answer
   the KB cannot justify is a wrong answer here.
2. **No fabrication.** Never invent a logger model, date, file name, citation,
   or number. If the KB returns nothing usable, say so.
3. **Unknown stays unknown.** A faithful *"I could not find this in the KB"* is a
   **good** outcome; a confident guess is a **failure**. Record what you
   searched and that it came up empty.
4. **Surface contradictions you do find.** If the KB returns conflicting values
   for the same fact, report *all* of them and which source each came from — do
   not silently pick one.
5. **Prior memory of a forbidden source is a stop condition.** If, while working
   on any question, you realize you already know the answer — or any ground-truth
   detail — from having previously seen a forbidden file (`eval/qa/answers/`,
   `eval/qa/README.md`, `raw/`, `eval/STORY.md`, or the generator script), do
   **not** answer from that memory. Stop, state which file(s) you recall and
   **when/how you learned them**, and exit the prediction process entirely.
   Report it to the operator so the run can be redone untainted.

## What you MAY do

- **Read** `eval/qa/questions/NN-q.md` (all 40, `01`–`40`). This is your only
  allowed input file.
- **Use the `<knowledge-base>` tools** to search, query, read pages, and follow
  links inside the KB. For the default KB these are the `mcp__gbrain__*` tools
  (e.g. `mcp__gbrain__search`, `mcp__gbrain__query`, `mcp__gbrain__recall`,
  `mcp__gbrain__get_page`, `mcp__gbrain__get_backlinks`,
  `mcp__gbrain__get_chunks`). Use whatever read tools the registered KB exposes.
- **Iterate.** Multi-hop questions (link-following, temporal, multi-modal) need
  several tool calls — follow the citations the KB returns rather than stopping
  at the first hit.

## What you MUST NOT do

To keep the score a true measure of the KB — not of your access to the key — you
are forbidden from reading any source of ground truth:

- ❌ `eval/qa/answers/` — the answer key.
- ❌ `eval/qa/README.md` — contains the question index, grading rules, and the
  list of planted inconsistencies (spoils the test).
- ❌ `raw/` — the source corpus the KB was ingested from.
- ❌ `eval/STORY.md`, `eval/STORY_zh.md`, `eval/STORY_ja.md` (the human-facing
  translations carry the same overview), `scripts/generation/scaffold-personal.py`,
  and any other corpus metadata or generator file.
- ❌ Any non-`<knowledge-base>` tool that reads files or the web.

If a question references a path like `raw/research/coral-resilience-paper.pdf`,
that is a **hint about what to ask the KB for** — not a file you may open. Query
the KB for that content.

## Output

Write one prediction file per question:

```
<knowledge-base>-files/qa/predictions/NN-p.md
```

(e.g. `gbrain-files/qa/predictions/01-p.md`). Use the **same zero-padded ID**
as the question. Create the directory if it does not exist. Produce all 40.

Each `NN-p.md` should contain:

````markdown
---
id: NN
knowledge_base: <knowledge-base>
question_file: eval/qa/questions/NN-q.md
---

# P<NN> — <short title>

## Answer

<Your answer, exactly as supported by KB retrieval. Be specific: names, models,
numbers, dates. If multiple sources disagree, list each value and its source.>

## Retrieval trail

<The KB tool calls you made and what each returned, in order — enough that a
reader can retrace how you got here. e.g.
- `mcp__gbrain__search("SST logger model")` → page coral-resilience-paper …
- `mcp__gbrain__get_page("coral-resilience-paper")` → §2.2 said "Onset HOBO
  U22-001 … 8 m … 30-minute intervals".>

## Confidence / coverage

<One of: **found** / **partial** / **not found in KB**, plus a one-line reason.
If "not found", list the queries you tried.>
````

## Tips

- **Multi-modal questions (22–30, 34, 40).** The KB's value is parsing the binaries
  (PDF / xlsx / PNG). Probe the KB for the binary's content, not just a Markdown
  mirror. Note in your trail whether the answer came from the binary or a
  mirror.
- **Inconsistencies.** Some questions are designed so sources disagree. If your
  KB retrieval surfaces two different values for the same fact, that is a
  feature — report both rather than choosing one.
- **Cross-lingual questions (31–40).** Some queries are written in Japanese or
  Chinese, and some answers live only in Japanese/Chinese documents. Handle
  non-English queries directly; you may answer in English or in the query's
  language. Probe the KB for the CJK source content (PDFs / xlsx / CSV with JA
  or ZH text), not just an English mirror — some facts exist *only* in the
  non-English original (and one inconsistency, Q39, spans an EN and a JA source).
- **Done** = all 40 `NN-p.md` files exist under
  `<knowledge-base>-files/qa/predictions/`. Then hand off to the eval agent
  (`EVAL_INSTRUCTIONS.md`).
