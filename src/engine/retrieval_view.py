"""Retrieval-view composition shared by ingest, retain, and backfill.

Embeddings and lexical tokens are built from a metadata-prefixed view of a
chunk or memory so a document whose extracted body text is low quality (a
scanned PDF whose extraction is OCR noise) still surfaces when the query
matches its title or clean overview. Displayed text — document reading,
evidence excerpts — always stays the original extraction.
"""

from __future__ import annotations

# Bounded so the shared prefix cannot dominate the vector or cluster
# same-document chunks under MMR's redundancy penalty.
OVERVIEW_PREFIX_CHARS = 300


def retrieval_view_prefix(
    title: str | None, filename: str | None = None, overview: str | None = None
) -> str:
    """Compose ``"{title} | {filename} | {overview[:300]}"`` plus a newline.

    Empty parts are omitted and exact duplicates (title == filename) collapse
    to one. Returns an empty string (no prefix) when every part is empty.
    """
    parts: list[str] = []
    for part in (title, filename, (overview or "").strip()[:OVERVIEW_PREFIX_CHARS]):
        cleaned = (part or "").strip()
        if cleaned and cleaned not in parts:
            parts.append(cleaned)
    return " | ".join(parts) + "\n" if parts else ""


def retrieval_view(
    text: str, title: str | None, filename: str | None = None, overview: str | None = None
) -> str:
    """The text retrieval sees: metadata prefix + original text."""
    return retrieval_view_prefix(title, filename, overview) + text
