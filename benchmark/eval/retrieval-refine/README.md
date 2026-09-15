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

`run_ablation.py` executes current, source-isolated, source-local fusion,
hierarchical, and safety-lane rankings over one frozen candidate set. It reports
MRR, nDCG@5, Recall@5, unique-document coverage, measured p50/p95 runner latency,
and the selected configuration.

`run_scale.py` materializes the deterministic 30,000-record source/length
distribution and records bounded indexed candidates, the required live SQL-plan
assertion, peak memory, per-phase p50/p95/p99, deep outcome counts, and maximum
response bytes.

`failure_injection.py` executes the versioned `failure_matrix.json` contract for
routing, embedding, lexical storage, graph, temporal ranking, reranking,
evidence loading, MCP, and cancellation. It records truthful typed outcomes and
fallbacks and proves that all child tasks are cancelled and awaited.

`replay_cases.json` is shared by the MCP and Pi HTTP/SSE acceptance tests. It
freezes the automatic-driving incident plus knowledge, continuity, mixed, and
no-answer behavior, including exact document and citation identities.

`rollout_rehearsal.json` records every isolated migration transition with
document/parent/chunk counts, a stable protected-row checksum, dependency drain,
read enablement, and feature-off rollback state.
