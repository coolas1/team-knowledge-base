import pytest

from src.engine.interface import (
    Capabilities, DocumentRef, GraphData, GraphLink, GraphNode,
    IngestSource, NotSupported, RecallChunk, RecallRequest, RecallResult,
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
    chunk = RecallChunk(doc_id="d", title="t", chunk_text="c",
                        reranker_score=1.0, vector_score=0.5)
    res = RecallResult(chunks=[chunk], related_entities=[{"a": 1}], related_docs=[])
    g = GraphData(nodes=[GraphNode(name="n", type="T")],
                  links=[GraphLink(source="n", target="m", type="R")])
    assert res.chunks[0].doc_id == "d"
    assert g.nodes[0].name == "n"


def test_notsupported_is_exception():
    with pytest.raises(NotSupported):
        raise NotSupported("graph")
