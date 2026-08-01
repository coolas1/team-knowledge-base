import pytest

from src.engine.interface import (
    Capabilities,
    GraphData,
    GraphLink,
    GraphNode,
    IngestSource,
    KnowledgeQueryRequest,
    KnowledgeQueryResult,
    KnowledgeSource,
    NotSupported,
    RecallChunk,
    RecallRequest,
    RecallResult,
)


def test_capabilities_defaults():
    c = Capabilities()
    assert c.graph is False
    assert c.partial_update is False
    assert c.multimodal is False
    assert c.namespace is False


def test_ingest_source_carries_bytes():
    s = IngestSource(name="x.md", data=b"hello")
    assert s.data == b"hello"
    assert s.path is None


def test_dataclasses_roundtrip():
    req = RecallRequest(query="q", top_k=5)
    assert req.top_k == 5
    chunk = RecallChunk(
        doc_id="d", title="t", chunk_text="c", reranker_score=1.0, vector_score=0.5
    )
    res = RecallResult(chunks=[chunk], related_entities=[{"a": 1}], related_docs=[])
    g = GraphData(
        nodes=[GraphNode(name="n", type="T")],
        links=[GraphLink(source="n", target="m", type="R")],
    )
    assert res.chunks[0].doc_id == "d"
    assert g.nodes[0].name == "n"


def test_notsupported_is_exception():
    with pytest.raises(NotSupported):
        raise NotSupported("graph")


def test_knowledge_query_defaults_to_answer_oriented_auto_strategy():
    request = KnowledgeQueryRequest(query="compare recent progress")
    source = KnowledgeSource(
        memory_id="memory-1",
        memory_type="world",
        doc_id="document-1",
        title="week.md",
        chunk_text="progress",
    )
    result = KnowledgeQueryResult(strategy_used="reflect", sources=[source])

    assert request.strategy == "auto"
    assert request.mode == "deep"
    assert request.needs_answer is True
    assert result.sources[0].memory_id == "memory-1"
