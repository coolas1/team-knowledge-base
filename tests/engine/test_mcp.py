import pytest

from src.engine import mcp as mcp_mod
from src.engine.interface import (
    GraphData,
    GraphNode,
    KnowledgeQueryResult,
    KnowledgeSource,
    RecallResult,
)
from tests.conftest import FakeKnowledgeBase


@pytest.fixture
def fake_kb():
    kb = FakeKnowledgeBase()
    mcp_mod.set_kb(kb)
    yield kb
    mcp_mod._kb = None


async def test_search_tool_returns_chunks(fake_kb):
    seen = []

    async def recall(request):
        seen.append(request)
        return RecallResult()

    fake_kb.recall = recall
    res = await mcp_mod.search("acme", top_k=7)
    assert res == {"chunks": [], "related_entities": [], "related_docs": []}
    assert seen[0].query == "acme"
    assert seen[0].top_k == 7


async def test_query_knowledge_forwards_unified_request():
    class FakeQueryService:
        request = None

        async def query(self, request):
            self.request = request
            return KnowledgeQueryResult(
                strategy_used="reflect",
                answer="answer",
                sources=[
                    KnowledgeSource(
                        memory_id="m1",
                        memory_type="chunk",
                        doc_id="d1",
                        title="Doc",
                        chunk_text="context",
                    )
                ],
            )

    service = FakeQueryService()
    mcp_mod.set_query_service(service)
    try:
        out = await mcp_mod.query_knowledge(
            "acme", strategy="reflect", mode="fast", top_k=3
        )
    finally:
        mcp_mod._query_service = None

    assert out["answer"] == "answer"
    assert out["sources"][0]["memory_id"] == "m1"
    assert service.request.strategy == "reflect"
    assert service.request.mode == "fast"
    assert service.request.top_k == 3


async def test_get_document_missing_returns_error(fake_kb):
    res = await mcp_mod.get_document("nope")
    assert "error" in res


async def test_query_graph_missing_returns_error(fake_kb):
    res = await mcp_mod.query_graph("ghost")
    assert "error" in res


async def test_query_graph_found(fake_kb):
    fake_kb.graph = GraphData(nodes=[GraphNode(name="Acme", type="Company")])
    res = await mcp_mod.query_graph("Acme", include_neighbors=False)
    assert res["name"] == "Acme"
    assert res["type"] == "Company"
    assert "relations" in res


async def test_upload_document(fake_kb):
    res = await mcp_mod.upload_document("x.md", "hello world")
    assert res["title"] == "x.md"
    assert list(fake_kb.raw.values())[0] == b"hello world"


async def test_list_documents_tool(fake_kb):
    res = await mcp_mod.list_documents()
    assert {"total", "items"} <= set(res)


async def test_remove_document_tool(fake_kb):
    res = await mcp_mod.remove_document("abc")
    assert res == {"removed": "abc"}


async def test_get_full_graph_tool(fake_kb):
    fake_kb.graph = __import__(
        "src.engine.interface", fromlist=["GraphData"]
    ).GraphData()
    res = await mcp_mod.get_full_graph()
    assert res == {"nodes": [], "links": []}
