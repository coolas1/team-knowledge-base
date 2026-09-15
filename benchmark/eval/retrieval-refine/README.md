# Retrieval refinement benchmark

This fixture freezes the failure modes that motivated
`separate-conversation-memory-from-knowledge-evidence`. It is synthetic and
contains no production query or evidence text.

Run the reproducibility/schema check from the repository root:

```powershell
$env:UV_CACHE_DIR='.uv-cache'
uv run python benchmark/eval/retrieval-refine/validate.py
```

`baseline.json` records the pre-change behavior of the fixture. A result may
only replace it when the case IDs and corpus version match. Runtime evaluations
append a separate report containing build SHA, configuration, corpus hash,
source ranks, normalized scores, citation IDs, phase timing, payload bytes and
active-memory counts; source text is deliberately excluded.

`continuity_cases.json` freezes conversation and mixed-route adversaries across
relevant/irrelevant, active/stale, confirmed/unconfirmed, and superseded memory.
`metrics.py` scores route accuracy and conversation precision@3 from any runner
that emits the documented `fixture_result` shape; the embedded fixture results
are schema/metric smoke data, not a production-quality claim.

`metadata_cases.json` independently scores document Recall@5, useful original
passages, and truthful metadata-only disclosure across title, filename,
overview, tags, entities, noisy OCR, implicit topics, and multilingual queries.

`retention_events.json` replays contamination risks through the real retention
policy primitives. Its gate checks the authoritative active-memory count and
provenance after assistant/tool rejection, preference supersession, deduplication,
confirmed-plan retention, and expiry.

`honesty_cases.json` versions zero-tolerance gates for false positives,
unsupported answers, and fabricated citations on no-answer, weak-overlap, and
metadata-only queries.
