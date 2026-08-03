---
name: tkb-reflective-research
description: Perform complex Hindsight research over the Team Knowledge Base. Use for ambiguous, comparative, temporal, multi-step, or reflective questions that need recall and reflect.
---

# TKB Reflective Research

1. Break the question into the minimum useful subquestions.
2. Call `tkb_query_knowledge` with `strategy=reflect`, `mode=deep`, and
   `needs_answer=false` when the task needs synthesis or reflection.
3. Use `tkb_search_deep` or `strategy=recall` to retrieve evidence for the
   important conclusions produced by reflection.
4. Read only the most relevant complete documents.
5. Query a named graph entity only when relationships materially help.
6. Stop when evidence is sufficient or a tool/time limit is reported.

The final answer must distinguish retrieved facts from your synthesis and list
the supporting document title and `doc_id`. Never invent a source.
