# Memory parity baseline

This corpus is a labelled acceptance checklist, not a claim of model accuracy.
Cases describe ordered source events and fault injection as plain text. A runner
must translate these into actual retain/query/delete/restart operations; passing
the complete case text to a model as a question is **not** a parity evaluation.
Use fresh isolated data for each case and each repetition. Never run fault or
deletion cases against production data.

Validate the fixed corpus from the repository root:

```powershell
uv run python benchmark/memory-parity/validate.py
```

`baseline.json` captures the investigation baseline and configuration fingerprints.
It contains no credentials or endpoint URLs. Its status is `not_run`; no upstream
or TKB execution result is implied. Before an actual run, create a separate run
manifest with both actual commits and dirty patch fingerprints, corpus hash,
resolved model/provider versions, sanitized effective configuration, embedding
dimension, timestamps, and environment identifier. Do not overwrite the baseline.

For each batch, execute its subset on upstream and TKB once using the same
model/settings. Add a targeted rerun only for a concrete failure or new risk.
Collect case ID, repetition, engine, result, cited source IDs,
deterministic checks, human-reviewable semantic score, duration, token usage,
errors, and operation IDs. Keep retrieved evidence separate from actual citations.
Unavailable models/services mean `not_run`, never pass. B1 scope cases that require
later consolidation remain pending until B3; B1 verifies the scope matching
contract and isolation paths that exist at that point.

Acceptance: deterministic isolation/idempotency/deletion/reference/fault checks
all pass; each semantic category scores at least 90% and no more than five
percentage points below the upstream mean. Report p50/p95 and usage separately.
The corpus loader only verifies labels, coverage, uniqueness and fingerprint;
future batch execution adapters and result reports are separate deliverables.

### B2 execution tools

`run_extraction.py --upstream ../../hindsight --output <fresh-directory>` runs the
8 attribution/time cases once on each engine by default. It invokes upstream's actual
`extract_facts_from_text` and TKB's actual extraction stage with the configured
model. Source/ingestion control lines are removed from source text; source time is
passed through the API. This is extraction-stage evidence, not full retain/query
or operation-fault evidence. Successful requests still require semantic review.
Dependencies for upstream execution may be installed into `.cache/parity-deps`
and exposed through PYTHONPATH; each manifest records resolved package versions.

For actual Pi process/network faults, build `src/extensions/pi-agent`, then run
`node scripts/delivery-fault-smoke.mjs run <fresh-absolute-directory>` from that
directory. It uses the production transcript store, delivery worker and MCP client
in independent Node processes. A loopback MCP receiver fsyncs a test ledger before
acknowledging. The exercise covers unavailable network followed by process restart,
and process exit after remote acknowledgement but before local acknowledgement.
It asserts two wire deliveries produce one durable remote record, acknowledged
turns stop sending, and unfinished turns are excluded. The receiver is a test
ledger, not PostgreSQL; engine queue and lease integration tests remain required.

### B3 execution tool

`run_consolidation.py --upstream ../../hindsight --output <fresh-directory>` runs
the fixed B3 cases once. It calls the checked-out upstream's real consolidation
system prompt and constrained response model, and TKB's real B3 action prompt and
schema, through the same configured model. The adapter treats each labelled source
segment as an already extracted atomic fact, so it measures consolidation semantics;
PostgreSQL tests separately prove outbox, version, lease, history and deletion rules.
Use `--case <id>` only to rerun a concrete failed row and preserve that run beside
the original evidence.

### B4 execution tool

`run_retrieval_contract.py --output <fresh-directory>` compares the legacy and
extended request paths through the production `RecallEngine` with deterministic
ports. It reports p50/p95 and output/model token use without network variance.
This measures contract overhead; PostgreSQL tests prove filter, freshness, source
deletion, scope, and expansion behavior, while B7 retains the full relevance gate.
