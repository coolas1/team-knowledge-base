---
name: tkb-source-citation
description: Format a Team Knowledge Base answer with traceable document citations. Use when producing the final response from retrieved TKB evidence.
---

# TKB Source Citation

For every material claim, preserve the source identity returned by TKB.
Conclude with a compact source list in this format:

```text
来源：
- <title> (doc_id: <uuid>)
```

Deduplicate repeated documents. Do not cite an entity, memory, or document that
was not returned by a tool. When sources disagree, state the disagreement and
cite both. When no source supports the answer, state that the knowledge base
does not contain sufficient evidence.
