"""Durable source chunks with their original extraction provenance."""

from dataclasses import asdict, replace
from datetime import datetime
import json

from src.engine.components.chunker import Chunk, chunk_text

from .types import RetainInput


def memory_signature(
    *,
    text,
    memory_type,
    is_source_chunk,
    occurred_start=None,
    occurred_end=None,
    location=None,
    speaker_role="unknown",
    modality="unknown",
) -> str:
    return json.dumps(
        [
            text,
            memory_type,
            is_source_chunk,
            occurred_start.isoformat() if occurred_start else None,
            occurred_end.isoformat() if occurred_end else None,
            location,
            speaker_role,
            modality,
        ],
        ensure_ascii=False,
    )


def remember_ids(snapshot: dict, memories) -> None:
    """Persist identity with its source block, independent of future positions."""
    for item in snapshot["chunks"]:
        item["memory_ids"] = {}
    for memory in memories:
        if memory.chunk_index < 0:
            continue
        item = snapshot["chunks"][memory.chunk_index]
        item["chunk_id"] = memory.metadata["chunk_id"]
        signature = memory_signature(
            text=memory.text,
            memory_type=memory.memory_type,
            is_source_chunk=memory.is_source_chunk,
            occurred_start=memory.occurred_start,
            occurred_end=memory.occurred_end,
            location=memory.location,
            speaker_role=memory.metadata.get("speaker_role", "unknown"),
            modality=memory.metadata.get("modality", "unknown"),
        )
        item["memory_ids"].setdefault(signature, []).append(memory.id)


def legacy_content_snapshot(document, memories, source_context: dict) -> dict:
    """Recover saved source blocks, never replacing them with newer file text."""
    grouped = {}
    for memory in memories:
        if memory.chunk_index >= 0:
            grouped.setdefault(memory.chunk_index, []).append(memory)
    result = {"version": 1, "content": "", "chunks": [], "recovered_legacy": True}
    for rows in grouped.values():
        source_row = next((row for row in rows if row.is_source_chunk), rows[0])
        metadata = dict(source_row.metadata_json or {})
        saved = {**source_context, **metadata}
        timestamp = saved.get("source_timestamp")
        source = RetainInput(
            document_id=str(document.id),
            title=metadata.get("title", document.title),
            content=source_row.source_text,
            file_type=metadata.get("file_type", document.file_type),
            source_type=metadata.get("source_type", "upload"),
            context=source_row.context,
            tags=tuple(document.tags or []),
            agent_name=saved.get("agent_name"),
            speakers=saved.get("speakers", {}),
            source_timestamp=datetime.fromisoformat(timestamp) if timestamp else None,
            reference_timezone=saved.get("reference_timezone", "UTC"),
            policy_version=saved.get("policy_version", 1),
        )
        item = content_snapshot(
            source, [Chunk(0, source.content, max(1, len(source.content) // 2))]
        )["chunks"][0]
        identities = {}
        for row in rows:
            row_metadata = row.metadata_json or {}
            signature = memory_signature(
                text=row.text,
                memory_type=row.memory_type,
                is_source_chunk=row.is_source_chunk,
                occurred_start=row.occurred_start,
                occurred_end=row.occurred_end,
                location=row.location,
                speaker_role=row_metadata.get("speaker_role", "unknown"),
                modality=row_metadata.get("modality", "unknown"),
            )
            identities.setdefault(signature, []).append(str(row.id))
        item["memory_ids"] = identities
        result["chunks"].append(item)
    # Retain exact raw text only when it contains all saved chunks in order.
    raw = document.raw_text or ""
    position = 0
    for item in result["chunks"]:
        position = raw.find(item["text"], position)
        if position < 0:
            break
        position += 1  # Adjacent source chunks may overlap.
    if position >= 0 and result["chunks"]:
        result["content"] = raw
    else:
        result["content"] = "\n\n".join(item["text"] for item in result["chunks"])
    return result


def content_snapshot(source: RetainInput, chunks: list[Chunk]) -> dict:
    # Request identity and publication controls are not source provenance.
    provenance = asdict(source)
    for key in (
        "content",
        "request_id",
        "expected_revision",
        "force_extraction",
        "update_mode",
    ):
        provenance.pop(key)
    if source.source_timestamp is not None:
        provenance["source_timestamp"] = source.source_timestamp.isoformat()
    # Isolate nested metadata from callers; also reject non-JSON metadata before
    # publishing a snapshot that cannot be persisted.
    provenance = json.loads(json.dumps(provenance, ensure_ascii=False))
    return {
        "version": 1,
        "content": source.content,
        "chunks": [
            {"text": chunk.text, "token_count": chunk.token_count, "source": provenance}
            for chunk in chunks
        ],
    }


def snapshot_chunks(snapshot: dict, *, policy_version: int):
    """Restore original anchors while applying today's extraction policy."""
    if snapshot.get("version") != 1:
        raise ValueError("unsupported retention content snapshot")
    chunks, sources = [], []
    for index, item in enumerate(snapshot["chunks"]):
        provenance = dict(item["source"])
        timestamp = provenance.get("source_timestamp")
        if timestamp is not None:
            provenance["source_timestamp"] = datetime.fromisoformat(timestamp)
        provenance["tags"] = tuple(provenance.get("tags", ()))
        source = RetainInput(content=item["text"], **provenance)
        chunks.append(Chunk(index, item["text"], item["token_count"]))
        sources.append(replace(source, policy_version=policy_version))
    return chunks, sources


def replace_snapshot(
    source: RetainInput, previous: dict, *, chunk_size: int, overlap: int
) -> dict:
    """Keep complete source blocks; only chunk gaps introduced by replacement.

    A match must start/end at a line boundary so a common word inside a changed
    sentence cannot inherit the old sentence's provenance. Each saved occurrence
    is consumed at most once. Exact replacement retains overlapping chunks too.
    """
    if previous.get("version") != 1:
        raise ValueError("unsupported retention content snapshot")
    if source.content == previous["content"]:
        return json.loads(json.dumps(previous))
    candidates = []
    for item in previous["chunks"]:
        start = 0
        while item["text"]:
            position = source.content.find(item["text"], start)
            if position < 0:
                break
            end = position + len(item["text"])
            if (position == 0 or source.content[position - 1] == "\n") and (
                end == len(source.content) or source.content[end] == "\n"
            ):
                candidates.append((position, end, item))
            start = position + 1
    candidates.sort(key=lambda value: (value[0], -(value[1] - value[0])))
    result = {"version": 1, "content": source.content, "chunks": []}
    cursor = 0
    used = set()

    def add_gap(text):
        fresh = replace(source, content=text)
        result["chunks"].extend(
            content_snapshot(
                fresh, chunk_text(text, chunk_size=chunk_size, overlap=overlap)
            )["chunks"]
        )

    for start, end, item in candidates:
        if start < cursor or id(item) in used:
            continue
        add_gap(source.content[cursor:start])
        result["chunks"].append(json.loads(json.dumps(item)))
        used.add(id(item))
        cursor = end
    add_gap(source.content[cursor:])
    return result
