## Context

See proposal.md — Why. Retrieval mechanics as verified on the incident:
recall fuses a semantic arm and a keyword arm by RRF (`final_score =
Σ 1/(rrf_k + rank)`, k=60 — hence the observed 0.012–0.031 scores in fast
mode); file chunks and memory units both compete. `_filter_by_relevance`
(`recall.py:730-761`) applies a semantic floor (`recall_min_semantic` 0.45)
but keeps any candidate with `keyword_score > 0` unconditionally, and the
deep-only rerank floor (`recall_min_score` 0.4) never runs in fast mode.
Arm scores are on different scales (memory BM25 vs chunk token-overlap), so
an absolute keyword floor is brittle. Documents carry clean LLM-written
overviews (`documents.overview`, analyzer output) that retrieval ignores.
Response assembly (`query.py`) embeds full document records in each
source's metadata and repeats every evidence text in `based_on`; the MCP
layer returns `asdict(result)` unbounded.

## Goals / Non-Goals

**Goals:**

- Only query-relevant evidence returns, documents and conversations judged
  identically; fewer-than-top_k results are acceptable.
- OCR-noisy documents findable via their clean metadata.
- Any search tool response, serialized whole, within a character budget.

**Non-Goals:**

- OCR/extraction quality; extractor-level denoising (superseded by
  retrieval views); conversation-memory retention and automatic recall;
  REST/BFF endpoints; ranking-model changes beyond the gate.

## Decisions

- **Gate on query-term coverage, not absolute keyword scores.** Replace the
  `keyword_score > 0` bypass in `_filter_by_relevance` with: keyword-only
  candidates pass iff the fraction of salient query terms present in their
  text (computed with the existing `lexical_tokens()`) meets
  `recall_min_term_coverage` (default 0.5) AND at least
  `recall_min_term_count` terms (default 2) match. Coverage is scale-free,
  so the arms' heterogeneous score scales don't matter. Candidates with a
  semantic score at/above `recall_min_semantic` pass regardless of keywords
  (unchanged); the deep-rerank floor stays as is. Single-term queries fall
  back to the semantic floor. Trace reports `filtered_count` (already
  threaded) so a short result list is explainable.
- **Retrieval view = metadata prefix + original text.** One composition
  helper builds `"{title} | {filename} | {overview[:300]}\n{text}"` for
  chunks and memory units; embeddings and lexical tokens are computed from
  the view; display text (doc viewer, evidence excerpts) stays the
  original. Prefix is bounded (300 chars) to limit same-document clustering
  under MMR's redundancy penalty. Alternatives rejected: a separate
  title-match candidate arm (new fusion machinery for what the semantic arm
  now does naturally); embedding-only denoising heuristics (fragile, leaves
  the keyword arm diluted); extractor stripping (risk of deleting real
  text, re-ingest dependency).
- **One-time backfill, all documents.** A CLI entry point recomputes
  retrieval views, re-embeds chunks and memories, and rebuilds lexical
  tokens for every current document (corpus ~48 docs — minutes). Uniform
  re-embedding avoids mixed-era vectors. It works from stored `chunk_text`
  + `documents.overview`, so documents whose upload files were lost (the
  09-03 rows, including both 自动驾驶 papers) are covered without
  re-upload.
- **Whole-response budget: slim structurally in the engine, enforce at the
  MCP boundary.** Structural fixes in `query.py` result assembly (metadata
  slimming; `based_on` opt-in via the existing `include` parameter, recall
  responses omit it; compact reflect `based_on` without repeated evidence
  text; `related_entities` capped by `engine_tools_entities_max`, default
  10). The three tools flow through two response shapes
  (`KnowledgeQueryResult` vs the legacy adapter payload), so the final
  character budget is enforced once, in a shared MCP-layer helper applied
  to all three tool responses: over budget → drop lowest-ranked sources
  first, set `payload_trimmed`/kept/dropped counts in the trace, mirroring
  `evidence_trimmed`. Budget knob `engine_tools_response_max_chars`
  (default 24,000 ≈ 6K tokens per search; two searches keep an iteration
  prefill an order of magnitude below the incident's 47.6K).
- **Metadata slimming detail.** `_source_from_candidate` keeps doc_id,
  title, source_type, session/turn ids, and scores; drops the embedded
  document record (the agent has `tkb_get_document` for detail).

## Risks / Trade-offs

- [Coverage floor too strict for broad single-concept queries] → minimum
  term count 2 with 50% coverage; single-term queries bypass to the
  semantic floor; both knobs configurable.
- [Gate returns zero sources for genuinely uncovered topics] → by design;
  trace reports filtered counts, and the agent can reformulate or use deep
  search.
- [Shared prefix clusters same-document chunks for MMR] → prefix bounded to
  300 chars; for "which documents exist" queries fewer per-doc chunks is
  desirable; revisit if multi-section deep queries thin out.
- [Backfill rewrites retrieval state for the whole corpus] → embeddings are
  recomputed from the same model and stored text; rollback = re-run with
  the old composition or restore from the pre-deploy pipeline backup.
- [Dropped fields break consumers] → `based_on` stays available via
  `include`; BFF/REST untouched; agent skills read `sources` first.

## Migration Plan

Code + config defaults through the pipeline as usual; after deploy, run the
backfill once on the LAN, then replay the 2026-09-12 question (both papers
surface, no unrelated sources, responses within budget, both turns well
under 300s) and record under `bench/`. Rollback = revert and redeploy;
re-run backfill only if reverting the composition change.
