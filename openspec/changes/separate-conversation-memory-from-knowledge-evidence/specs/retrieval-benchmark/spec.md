## ADDED Requirements

### Requirement: Benchmark measures source isolation and authority
The benchmark SHALL contain knowledge, conversation-continuity, mixed, and uncovered queries over a corpus where short conversation facts deliberately share terms with longer documents. It SHALL score route accuracy, conversation leakage into document evidence, document citation correctness, and unsupported-answer rate.

#### Scenario: Knowledge query with attractive conversation match
- **WHEN** a short conversation fact has stronger lexical overlap than the relevant document
- **THEN** the benchmark verifies that document evidence is returned and conversation leakage is zero for the knowledge route

### Requirement: Benchmark measures hierarchical discovery
The benchmark SHALL include documents whose relevant topic appears in title, filename, overview, tags, or entities but not verbatim in the best evidence chunk. It SHALL measure document recall and passage usefulness independently.

#### Scenario: Metadata-only topic match
- **WHEN** a query names a topic present only in document metadata
- **THEN** the benchmark scores whether the correct document and a useful original passage are returned

### Requirement: Benchmark measures memory contamination controls
The benchmark SHALL replay repeated answers, document summaries, changed preferences, duplicate turns, and unsupported assistant claims. It SHALL verify selective retention, deduplication, supersession, expiry behavior, and derivation provenance.

#### Scenario: Document answer is repeated across sessions
- **WHEN** an assistant summarizes the same document in multiple completed turns
- **THEN** the benchmark verifies that those summaries do not accumulate as independent authoritative memories

### Requirement: Benchmark measures retrieval performance at scale
The benchmark SHALL run at the current corpus size and at a projected corpus containing at least 30,000 retrieval records. It SHALL record per-phase latency percentiles, scanned and ranked candidate counts, deep success/degradation/fallback rates, timeout behavior, payload sizes, and result quality against a frozen baseline.

#### Scenario: Projected-scale run
- **WHEN** the scale suite runs with at least 30,000 records and indexed reads enabled
- **THEN** latency, candidate bounds, source mix, quality scores, and failures are persisted in a comparable run report

### Requirement: Release acceptance has explicit quality gates
A retrieval release SHALL NOT pass acceptance if knowledge-route conversation leakage is nonzero, required metadata-only documents regress, unsupported-answer rate increases beyond the recorded tolerance, payload bounds fail, or deadline/fallback contracts fail. Threshold values and approved exceptions SHALL be versioned with the run.

#### Scenario: One required gate fails
- **WHEN** any mandatory source-isolation, quality, payload, or deadline gate fails
- **THEN** the run is marked failed and deployment or migration promotion stops
