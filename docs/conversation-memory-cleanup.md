# Conversation memory cleanup

Historical conversation cleanup is a two-step, bank-scoped operation. It never
deletes uploaded documents or visible transcripts. Target memories are retired
from ordinary recall and remain available for audit.

Generate a content-free review manifest first:

```bash
uv run python -m src.engine.conversation_cleanup plan \
  --bank-id default-team \
  --output conversation-cleanup.json
```

Review the reported counts and preserve the manifest checksum as the rollback
artifact. If `unknown_blocks_retirement` is true, stop: unknown provenance must
be classified before any automatic retirement can run.

After backing up the database and explicitly approving the exact manifest,
execute it once:

```bash
uv run python -m src.engine.conversation_cleanup execute \
  --bank-id default-team \
  --manifest conversation-cleanup.json \
  --authorize-retirement
```

Execution rejects a changed checksum, another bank, unknown provenance, or any
record whose identity, lifecycle, derivation, or version changed after planning.
Generate a new manifest rather than bypassing those checks.
