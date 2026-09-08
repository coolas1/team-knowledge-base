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

For each batch, execute its subset on upstream and TKB three times using the same
model/settings. Collect case ID, repetition, engine, result, cited source IDs,
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
8 attribution/time cases three times on each engine. It invokes upstream's actual
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
