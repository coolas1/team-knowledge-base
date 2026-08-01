import pytest

from src.agent.engine_client import InProcessEngineClient, McpEngineClient
from src.engine.interface import (
    GraphData,
    GraphNode,
    KnowledgeQueryResult,
    KnowledgeSource,
)
from tests.conftest import FakeKnowledgeBase


async def test_inprocess_recall_returns_dict():
    kb = FakeKnowledgeBase()
    client = InProcessEngineClient(kb)
    out = await client.recall("acme")
    assert out == {"chunks": [], "related_entities": [], "related_docs": []}
    assert kb.recall_calls == ["acme"]


async def test_inprocess_query_forwards_unified_request():
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
    client = InProcessEngineClient(FakeKnowledgeBase(), query_service=service)
    out = await client.query(
        "acme", strategy="reflect", mode="fast", top_k=3, needs_answer=True
    )

    assert out["strategy_used"] == "reflect"
    assert out["sources"][0]["memory_id"] == "m1"
    assert service.request.query == "acme"
    assert service.request.mode == "fast"
    assert service.request.top_k == 3


async def test_inprocess_query_requires_service():
    client = InProcessEngineClient(FakeKnowledgeBase())
    with pytest.raises(RuntimeError, match="Hindsight"):
        await client.query("acme")


async def test_inprocess_ingest_returns_dict():
    kb = FakeKnowledgeBase()
    client = InProcessEngineClient(kb)
    out = await client.ingest("x.md", b"hello")
    assert out["title"] == "x.md"
    assert out["status"] == "indexed"


async def test_inprocess_get_graph_returns_dict():
    kb = FakeKnowledgeBase()
    kb.graph = GraphData(nodes=[GraphNode(name="n", type="T")])
    client = InProcessEngineClient(kb)
    out = await client.get_graph(None)
    assert out["nodes"][0]["name"] == "n"


async def test_mcp_recall_calls_search_tool(monkeypatch):
    client = McpEngineClient("http://localhost:8000/mcp")
    calls = []

    async def fake_call(tool, args):
        calls.append((tool, args))
        return {"chunks": [], "related_entities": [], "related_docs": []}

    monkeypatch.setattr(client, "_call", fake_call)
    out = await client.recall("acme", top_k=7)
    assert out["chunks"] == []
    assert calls == [("search", {"query": "acme", "top_k": 7})]


async def test_mcp_query_calls_unified_tool(monkeypatch):
    client = McpEngineClient("http://localhost:8000/mcp")
    calls = []

    async def fake_call(tool, args):
        calls.append((tool, args))
        return {"strategy_used": "reflect", "answer": "answer", "sources": []}

    monkeypatch.setattr(client, "_call", fake_call)
    out = await client.query(
        "acme", strategy="reflect", mode="fast", top_k=4, needs_answer=True
    )
    assert out["answer"] == "answer"
    assert calls == [
        (
            "query_knowledge",
            {
                "query": "acme",
                "strategy": "reflect",
                "mode": "fast",
                "top_k": 4,
                "needs_answer": True,
            },
        )
    ]


async def test_mcp_ingest_calls_upload_document(monkeypatch):
    client = McpEngineClient("http://localhost:8000/mcp")

    async def fake_call(tool, args):
        assert tool == "upload_document"
        assert args == {"file_name": "x.md", "content": "hello"}
        return {
            "id": "1",
            "title": "x.md",
            "file_type": "markdown",
            "status": "indexed",
        }

    monkeypatch.setattr(client, "_call", fake_call)
    out = await client.ingest("x.md", b"hello")
    assert out["title"] == "x.md"


async def test_mcp_get_graph_calls_query_graph(monkeypatch):
    client = McpEngineClient("http://localhost:8000/mcp")

    async def fake_call(tool, args):
        assert tool == "query_graph"
        return {"name": "Acme", "type": "Company"}

    monkeypatch.setattr(client, "_call", fake_call)
    out = await client.get_graph("Acme")
    assert out["name"] == "Acme"


async def test_inprocess_list_documents():
    kb = FakeKnowledgeBase()
    client = InProcessEngineClient(kb)
    out = await client.list_documents()
    assert {"total", "page", "page_size", "items"} <= set(out)


async def test_inprocess_remove_returns_removed():
    kb = FakeKnowledgeBase()
    client = InProcessEngineClient(kb)
    out = await client.remove("abc")
    assert out == {"removed": "abc"}


async def test_mcp_list_documents_calls_tool(monkeypatch):
    client = McpEngineClient("http://x/mcp")
    seen = []

    async def fake_call(tool, args):
        seen.append((tool, args))
        return {"total": 0, "page": 1, "page_size": 20, "items": []}

    monkeypatch.setattr(client, "_call", fake_call)
    out = await client.list_documents(page=2, page_size=5, file_type="markdown")
    assert out["total"] == 0
    assert seen == [
        (
            "list_documents",
            {"page": 2, "page_size": 5, "file_type": "markdown", "status": None},
        )
    ]


async def test_mcp_remove_calls_tool(monkeypatch):
    client = McpEngineClient("http://x/mcp")

    async def fake_call(tool, args):
        assert tool == "remove_document"
        assert args == {"doc_id": "abc"}
        return {"removed": "abc"}

    monkeypatch.setattr(client, "_call", fake_call)
    out = await client.remove("abc")
    assert out == {"removed": "abc"}


async def test_mcp_get_graph_none_calls_full_graph(monkeypatch):
    client = McpEngineClient("http://x/mcp")
    seen = []

    async def fake_call(tool, args):
        seen.append(tool)
        return {"nodes": [], "links": []}

    monkeypatch.setattr(client, "_call", fake_call)
    await client.get_graph(None)
    assert seen == ["get_full_graph"]
