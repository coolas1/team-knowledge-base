"""Mappings from the existing Hindsight-style payloads to engine DTOs."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.engine.interface import (
    GraphData,
    GraphLink,
    GraphNode,
    RecallChunk,
    RecallResult,
)


def _score(value: Any, default: float = 0.0) -> float:
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def map_recall(payload: Mapping[str, Any], *, top_k: int) -> RecallResult:
    """Map ``TkbMemory.recall`` output without leaking its internal DTOs."""

    raw_chunks = payload.get("chunks", {})
    chunks_by_id = raw_chunks if isinstance(raw_chunks, Mapping) else {}
    mapped: list[RecallChunk] = []
    related_docs: list[dict[str, Any]] = []
    seen_docs: set[str] = set()

    raw_results = payload.get("results", [])
    if not isinstance(raw_results, list):
        raw_results = []
    for item in raw_results[:top_k]:
        if not isinstance(item, Mapping):
            continue
        doc_id = str(item.get("document_id", ""))
        metadata = item.get("metadata", {})
        metadata = metadata if isinstance(metadata, Mapping) else {}
        title = str(metadata.get("title", ""))
        chunk_id = str(item.get("chunk_id", ""))
        source = chunks_by_id.get(chunk_id, {})
        source = source if isinstance(source, Mapping) else {}
        scores = item.get("scores", {})
        scores = scores if isinstance(scores, Mapping) else {}

        final_score = _score(scores.get("final"))
        mapped.append(
            RecallChunk(
                doc_id=doc_id,
                title=title,
                chunk_text=str(source.get("text") or item.get("text") or ""),
                reranker_score=_score(scores.get("reranker"), final_score),
                vector_score=_score(scores.get("semantic")),
            )
        )
        if doc_id and doc_id not in seen_docs:
            seen_docs.add(doc_id)
            related_docs.append({"id": doc_id, "title": title})

    raw_entities = payload.get("entities", {})
    if isinstance(raw_entities, Mapping):
        related_entities = [
            {"name": str(name), "state": state} for name, state in raw_entities.items()
        ]
    elif isinstance(raw_entities, list):
        related_entities = [
            dict(item) for item in raw_entities if isinstance(item, Mapping)
        ]
    else:
        related_entities = []

    return RecallResult(
        chunks=mapped,
        related_entities=related_entities,
        related_docs=related_docs,
    )


def map_entity_graph(payload: Mapping[str, Any]) -> GraphData:
    """Map the source Cytoscape-shaped entity graph into ``GraphData``."""

    nodes: list[GraphNode] = []
    labels: dict[str, str] = {}
    for raw in payload.get("nodes", []):
        if not isinstance(raw, Mapping):
            continue
        data = raw.get("data", raw)
        if not isinstance(data, Mapping):
            continue
        node_id = str(data.get("id", ""))
        label = str(data.get("label") or data.get("canonical_name") or node_id)
        labels[node_id] = label
        mention_count = data.get("mentionCount")
        description = (
            f"memory mentions: {mention_count}" if mention_count is not None else ""
        )
        nodes.append(
            GraphNode(name=label, type="memory_entity", description=description)
        )

    links: list[GraphLink] = []
    for raw in payload.get("edges", payload.get("links", [])):
        if not isinstance(raw, Mapping):
            continue
        data = raw.get("data", raw)
        if not isinstance(data, Mapping):
            continue
        source_id = str(data.get("source", ""))
        target_id = str(data.get("target", ""))
        links.append(
            GraphLink(
                source=labels.get(source_id, source_id),
                target=labels.get(target_id, target_id),
                type=str(data.get("linkType") or data.get("type") or "related"),
                description=(
                    f"weight: {data['weight']}"
                    if data.get("weight") is not None
                    else ""
                ),
            )
        )
    return GraphData(nodes=nodes, links=links)
