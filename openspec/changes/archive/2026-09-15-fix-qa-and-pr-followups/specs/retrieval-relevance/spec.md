## Purpose

Defines how recall results are filtered for relevance: the semantic similarity floor that applies in every recall mode, the additional rerank-score gate in deep mode, the keyword-hit bypass, and the separate, lower floor used by conversation-memory recall.

## ADDED Requirements

### Requirement: Semantic floor applies in every mode
Every recall mode (fast, deep, auto-resolved) SHALL drop candidates whose semantic similarity is below the configured semantic floor, regardless of any reranker score. In deep mode the rerank-score gate SHALL apply in addition to the semantic floor, not instead of it.

#### Scenario: Deep mode with a divergent reranker
- **WHEN** a candidate has semantic similarity 0.2 (below the 0.45 floor) but a reranker score high enough that its clamped final score would pass the deep-mode score gate
- **THEN** the candidate is dropped in deep mode, consistent with fast mode

#### Scenario: Fast mode below the floor
- **WHEN** a candidate has semantic similarity below the floor and no reranker score
- **THEN** the candidate is dropped

### Requirement: Deep-mode rerank gate applies in addition
In deep mode, when a reranker score is available, a candidate SHALL additionally be dropped when its final (clamped) score is below the configured deep-mode score floor.

#### Scenario: Above semantic floor, below score floor
- **WHEN** a deep-mode candidate has semantic similarity above the semantic floor but a final score below the deep-mode score floor
- **THEN** the candidate is dropped

#### Scenario: Above both floors
- **WHEN** a deep-mode candidate passes both the semantic floor and the deep-mode score floor
- **THEN** the candidate is kept

### Requirement: Keyword hits bypass relevance gates
Candidates with a keyword (lexical) score above zero SHALL pass the relevance gates in every mode, unchanged.

#### Scenario: Keyword match with low semantics
- **WHEN** a candidate matches the query lexically but has semantic similarity below the floor
- **THEN** the candidate is kept in fast and deep mode

### Requirement: Conversation-memory recall uses its own floor
Recall performed for conversation memory SHALL apply a separately configured semantic floor (lower than the public-corpus floor by default), so tightening or loosening the public floor does not determine what memories are recalled. The public floor SHALL continue to govern public search and answer recall.

#### Scenario: Memory recalled below the public floor
- **WHEN** a conversation memory has semantic similarity 0.3 — below the public 0.45 floor but above the memory floor
- **THEN** it is recalled by conversation-memory recall and remains excluded from public search results

#### Scenario: Floors are independently configurable
- **WHEN** the public semantic floor is raised
- **THEN** conversation-memory recall behavior is unchanged, and vice versa
