## Why

On the LAN deployment (v0.3.0, 2026-09-12, session `01a09480`) an agent turn
asking for the 自动驾驶 papers returned polluted evidence and died on its
follow-up turn. Three verified causes: (1) the relevance gate exists
(`recall_min_semantic` 0.45) but an unconditional `keyword_score > 0` bypass
lets any candidate that matched a single BM25 token through — fermentation
recipes and sound-design notes surfaced beside one real paper; (2) the
second paper's scanned-PDF body is OCR noise, so its embeddings never
competed — yet the document's LLM-written `overview` is clean and was never
used by retrieval; (3) each search tool response ran ~50KB (metadata
embedding full document records, `based_on` duplicating every source's text,
uncapped entities/trace), so two searches produced a 47K-token prefill —
~3min per LLM iteration on this backend, and the follow-up turn hit
`time_limit`. Conversation memories are part of the corpus by design and
stay in — the failure was relevance gating and findability, not their
presence.

## What Changes

- **Relevance gate for search evidence**: close the keyword bypass; a
  candidate that only matches keywords must pass a query-term coverage floor
  (≥50% of salient query terms, minimum 2) — scale-free, so it works across
  the arms' different score scales. Documents and conversation memories are
  judged identically on content; the gate MAY return fewer than `top_k`
  sources (down to zero) rather than pad with noise.
- **Metadata-enriched retrieval views**: chunks and memories get a
  retrieval view composed of the document's title, filename, and clean
  overview prefix ahead of the original text; embeddings and lexical tokens
  are built from the retrieval view while displayed/read text stays the
  original. A one-time backfill recomputes retrieval views and re-embeds
  all documents (corpus ~48 docs; no re-upload needed — stored text plus
  existing overview suffice, which also covers the docs whose upload files
  are lost).
- **Whole-response payload budget for every search tool** (`tkb_search`,
  `tkb_search_fast`, `tkb_search_deep`): the entire serialized response
  SHALL stay within a configured character budget — source metadata carries
  identifying fields instead of the full document record; evidence is not
  duplicated between `sources` and `based_on`; `related_entities` and
  `trace` are capped/compacted; trimming is reported.

Non-goals: extractor-level OCR denoising (superseded by retrieval views);
OCR/extraction quality itself; conversation-memory retention and automatic
pre-response recall; REST/BFF search endpoints; changing conversation
memory's inclusion in search.

## Capabilities

### New Capabilities

- `search-evidence`: what evidence the agent search tools return — only
  relevant evidence (uniform gate over documents and conversations), and
  documents findable through their metadata when body text is low quality.

### Modified Capabilities

- `knowledge-tools`: extends payload bounding from per-excerpt evidence to
  the whole serialized search response, and forbids duplicating evidence
  within one response.

## Impact

- **Code:** `src/engine/hindsight_components/recall.py` (gate in
  `_filter_by_relevance`, options in `config.py`), retrieval-view
  composition + embedding/lexical paths in the ingest pipeline
  (`src/engine/graphrag/pipeline.py`, `components/embedder.py`) and the
  memory retain path (`hindsight_components/repository.py`), a backfill
  entry point (`src/engine/cli.py` or the existing rebuild runner),
  `src/agent/tkb/mcp/server.py` (response slimming + budget helper),
  `src/engine/hindsight_components/query.py` (metadata slimming, `based_on`
  opt-in), `config/settings.py` + `config/app.yaml` (budget knobs).
- **Compatibility:** search tool responses get smaller and drop redundant
  fields; recall may return fewer than `top_k` sources. The BFF/REST search
  endpoints are unchanged. One-time backfill re-embeds all chunks and
  memories.
- **Validation:** unit tests for the gate, retrieval views, and budgets;
  post-deploy backfill + replay of the 2026-09-12 question (both papers
  surface, no unrelated sources, turn completes well under budget) recorded
  under `bench/`.
