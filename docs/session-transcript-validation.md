# Session transcript validation

Validation date: 2026-09-05

## Existing-volume recovery audit

The verification command mounted the deployed `team-kb_piagentdata` volume
read-only, rebuilt journals in an isolated temporary directory, and compared
SHA-256 hashes for every SDK session JSONL before and after recovery. It did not
print conversation content.

```text
sessions=18
compactedSessions=8
sdkVisibleMessages=67
recoveredVisibleMessages=67
recoveryErrors=0
sourceFilesChanged=0
```

The currently deployed older runtime has no transcript journals yet. The
read-only inventory therefore reported 18 missing journals and 18 count
mismatches before simulated recovery. This is the expected pre-deployment state;
lazy recovery creates the journals after the updated runtime is deployed.

## Fault injection and checks

- Pi Agent security gate, production dependency audit (0 vulnerabilities),
  typecheck, 75 unit/integration-style tests, and build
  passed. Fixtures cover compacted and branched SDK sessions, model preflight and
  provider failures, cancellation, SSE disconnect, concurrent duplicate retry,
  torn/oversized append, process restart, and completed-turn memory retention.
- Web client: 18 tests and the Vite production build passed. Reducer coverage
  includes optimistic, accepted, completed, failed, cancelled, interrupted,
  unsaved, replayed, and reconciled rows.
- FastAPI BFF: Ruff lint/format checks and 14 focused tests passed. Tests cover
  legacy and idempotent message bodies, terminal SSE forwarding, and closing the
  upstream response after downstream disconnect.
- `docker compose config --quiet` and
  `openspec validate preserve-session-transcript-history --strict` passed.

The Vite build retains its existing warning that the main bundle exceeds 500
kB. The focused Python run retains Starlette's existing TestClient deprecation
warning; neither warning is caused by transcript behavior.
