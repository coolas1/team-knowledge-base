import pytest

from src.agent.engine_client import InProcessEngineClient, McpEngineClient
from src.engine.interface import GraphData, GraphNode
from tests.conftest import FakeKnowledgeBase


async def test_inprocess_recall_returns_dict():
    kb = FakeKnowledgeBase()
    client = InProcessEngineClient(kb)
    out = await client.recall("acme")
    assert out == {"chunks": [], "related_entities": [], "related_docs": []}
    assert kb.recall_calls == ["acme"]


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
    assert calls == [("search", {"query": "acme"})]


async def test_mcp_ingest_calls_upload_document(monkeypatch):
    client = McpEngineClient("http://localhost:8000/mcp")
    async def fake_call(tool, args):
        assert tool == "upload_document"
        assert args == {"file_name": "x.md", "content": "hello"}
        return {"id": "1", "title": "x.md", "file_type": "markdown", "status": "indexed"}
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
