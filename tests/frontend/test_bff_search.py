import pytest
from fastapi.testclient import TestClient

from src.frontend.webapp.server import app as app_mod, deps
from src.engine.interface import KnowledgeQueryResult, KnowledgeSource
from tests.conftest import FakeKnowledgeBase


@pytest.fixture
def client(monkeypatch):
    async def _noop():
        pass

    monkeypatch.setattr(deps, "startup", _noop)
    monkeypatch.setattr(deps, "shutdown", _noop)
    app_mod.app.dependency_overrides[deps.get_kb] = lambda: FakeKnowledgeBase()
    app_mod.app.dependency_overrides[deps.get_plugin] = lambda: None
    with TestClient(app_mod.app) as c:
        yield c
    app_mod.app.dependency_overrides.clear()


def test_search(client):
    res = client.post("/api/search", json={"query": "acme"})
    assert res.status_code == 200
    out = res.json()
    assert "chunks" in out and "related_entities" in out and "related_docs" in out


def test_search_rejects_blank_query(client):
    response = client.post("/api/search", json={"query": "   "})
    assert response.status_code == 422


def test_search_rejects_invalid_mode(client):
    response = client.post("/api/search", json={"query": "acme", "mode": "turbo"})
    assert response.status_code == 422


@pytest.mark.parametrize(
    ("route", "expected_chunks"),
    [
        ("knowledge", ["doc-memory"]),
        ("conversation", ["chat-memory"]),
        ("mixed", ["doc-memory", "chat-memory"]),
    ],
)
def test_search_api_preserves_grouped_sources(client, route, expected_chunks):
    document = KnowledgeSource(
        memory_id="doc-memory",
        memory_type="world",
        doc_id="doc-1",
        title="document",
        chunk_text="document evidence",
    )
    conversation = KnowledgeSource(
        memory_id="chat-memory",
        memory_type="experience",
        doc_id="chat-1",
        title="conversation",
        chunk_text="conversation context",
        authority="conversation",
        source_group="conversation_context",
    )

    class QueryService:
        async def query(self, _request):
            return KnowledgeQueryResult(
                strategy_used="recall",
                sources=[document],
                document_evidence=[document],
                conversation_context=[conversation],
                route_used=route,
            )

    app_mod.app.dependency_overrides[deps.get_query] = lambda: QueryService()
    response = client.post("/api/search", json={"query": "acme", "route": route})

    assert response.status_code == 200
    payload = response.json()
    assert [item["memory_id"] for item in payload["chunks"]] == expected_chunks
    assert [item["memory_id"] for item in payload["document_evidence"]] == [
        "doc-memory"
    ]
    assert [item["memory_id"] for item in payload["conversation_context"]] == [
        "chat-memory"
    ]
    assert payload["document_evidence"][0]["metadata"]["authority"] == "document"
    assert (
        payload["conversation_context"][0]["metadata"]["authority"]
        == "conversation"
    )


def test_search_api_conversation_only_does_not_require_documents(client):
    conversation = KnowledgeSource(
        memory_id="chat-memory",
        memory_type="experience",
        doc_id="chat-1",
        title="conversation",
        chunk_text="conversation context",
        authority="conversation",
        source_group="conversation_context",
    )

    class QueryService:
        async def query(self, _request):
            return KnowledgeQueryResult(
                strategy_used="recall",
                conversation_context=[conversation],
                route_used="conversation",
            )

    app_mod.app.dependency_overrides[deps.get_query] = lambda: QueryService()
    response = client.post(
        "/api/search", json={"query": "acme", "route": "conversation"}
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["document_evidence"] == []
    assert [item["memory_id"] for item in payload["chunks"]] == ["chat-memory"]
