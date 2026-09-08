# Deep search resilience validation

Validation was run on 2026-09-05 against the development PostgreSQL service.
The application pool was warmed with `init_db()` before request timings, which
matches service startup. The one-time host process connection setup is excluded
from retrieval latency.

## Results

| Scenario | Result |
| --- | ---: |
| Existing active memories backfilled | 2,817 |
| Remaining lexical rows after backfill | 0 |
| Synthetic scale set | 30,000 memories |
| Indexed keyword query p95, 10 runs | 99.18 ms |
| Python BM25 candidate cap | 300 |
| Warm fast recall | 462.22 ms |
| Warm deep recall with unavailable host-side model endpoints | 497.87 ms |
| Forced analysis, embedding, and rerank timeout | 307.73 ms |

The warm deep request returned ten keyword-backed sources with
`outcome=degraded`; query analysis, embedding, and neural reranking were
reported as failed without discarding keyword evidence. The forced-timeout
request returned ten sources with the three model phases reported as
`timed_out`. Both completed well inside the 45-second service budget.

The 30,000-row integration test also verified that indexed reads cannot be
enabled while null lexical rows exist, the backfill resumes after a committed
partial batch, a second run updates zero rows, and cleanup preserves the rest of
the database.

## Automated checks

- Python Ruff and the full default pytest suite
- Pi Agent security gate, TypeScript typecheck, Vitest suite, and production build
- Docker Compose configuration rendering
- OpenSpec strict validation
- Live PostgreSQL lexical backfill and 30,000-row scale integration test
