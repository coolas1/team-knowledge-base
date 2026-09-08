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
