import pytest
from mcp.server.transport_security import TransportSecurityMiddleware

from src.engine import mcp as mcp_mod
from src.engine.interface import (
    ConversationEnqueueResult,
    ConversationForgetResult,
    ConversationMemoryDiagnostics,
    ConversationMemoryItem,
    ConversationMemoryRecallResult,
    GraphData,
    GraphNode,
    IngestSource,
    KnowledgeQueryResult,
    KnowledgeSource,
    RecallResult,
)
from tests.conftest import FakeKnowledgeBase


def test_mcp_transport_allows_compose_hosts_and_rejects_unknown_hosts():
    settings = mcp_mod.mcp.settings.transport_security
    assert settings is not None
    assert settings.enable_dns_rebinding_protection is True

    security = TransportSecurityMiddleware(settings)
    assert security._validate_host("webapp:8000") is True
    assert security._validate_host("team-kb-webapp:8000") is True
    assert security._validate_host("attacker.example:8000") is False


@pytest.fixture
def fake_kb():
    kb = FakeKnowledgeBase()
    mcp_mod.set_kb(kb)
    mcp_mod.set_query_service(None)
    mcp_mod.set_conversation_memory_service(None)
    yield kb
    mcp_mod._kb = None
    mcp_mod._query_service = None
    mcp_mod._conversation_memory_service = None


class FakeConversationMemoryService:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.calls = []

    async def recall_conversation_memory(self, request):
        self.calls.append(("recall", request))
        if self.fail:
            raise RuntimeError("provider secret")
        return ConversationMemoryRecallResult(
            memories=[
                ConversationMemoryItem(
                    memory_id="memory-1",
                    text="User prefers blue",
                    memory_type="world",
                    document_id="document-1",
                    session_id="session-1",
                    turn_id="turn-1",
                    score=0.8,
                )
            ]
        )

    async def enqueue_conversation_turn(self, turn):
        self.calls.append(("enqueue", turn))
        if self.fail:
            raise RuntimeError("repository secret")
        return ConversationEnqueueResult(document_id="document-1", status="pending")

    async def forget_conversation_memory(self, request):
        self.calls.append(("forget", request))
        if self.fail:
            raise RuntimeError("repository secret")
        return ConversationForgetResult(
            session_id=request.session_id, cancelled_jobs=1, deleted_documents=1
        )

    async def conversation_memory_diagnostics(self):
        if self.fail:
            raise RuntimeError("repository secret")
        return ConversationMemoryDiagnostics(enabled=True, pending=2)


async def test_search_tool_returns_chunks(fake_kb):
    seen = []

    async def recall(request):
        seen.append(request)
        return RecallResult()

    fake_kb.recall = recall
    res = await mcp_mod.search("acme", top_k=7)
    assert res["chunks"] == []
    assert res["related_entities"] == []
    assert res["related_docs"] == []
    assert res["answer"] is None
    assert seen[0].query == "acme"
    assert seen[0].top_k == 7


async def test_private_conversation_memory_operations_map_success(fake_kb):
    service = FakeConversationMemoryService()
    mcp_mod.set_conversation_memory_service(service)

    recalled = await mcp_mod.recall_conversation_memory("preferred color", top_k=3)
    enqueued = await mcp_mod.enqueue_conversation_turn(
        "session-1", "turn-1", "Remember blue", "Understood"
    )
    forgotten = await mcp_mod.forget_conversation_memory("session-1")
    status = await mcp_mod.get_conversation_memory_status()

    assert recalled["memories"][0]["session_id"] == "session-1"
    assert enqueued == {"document_id": "document-1", "status": "pending"}
    assert forgotten["deleted_documents"] == 1
    assert status["pending"] == 2
    assert [name for name, _ in service.calls] == ["recall", "enqueue", "forget"]


async def test_conversation_memory_operations_validate_and_handle_disabled(fake_kb):
    with pytest.raises(ValueError, match="query"):
        await mcp_mod.recall_conversation_memory(" ")
    with pytest.raises(ValueError, match="top_k"):
        await mcp_mod.recall_conversation_memory("query", top_k=0)
    with pytest.raises(ValueError, match="session_id"):
        await mcp_mod.enqueue_conversation_turn("", "turn-1", "user", "assistant")
    with pytest.raises(RuntimeError, match="disabled"):
        await mcp_mod.recall_conversation_memory("query")
    assert await mcp_mod.get_conversation_memory_status() == {
        "enabled": False,
        "pending": 0,
        "processing": 0,
        "completed": 0,
        "failed": 0,
        "cancelled": 0,
    }


@pytest.mark.parametrize(
    ("operation", "message"),
    [
        (lambda: mcp_mod.recall_conversation_memory("query"), "recall failed"),
        (
            lambda: mcp_mod.enqueue_conversation_turn(
                "session-1", "turn-1", "user", "assistant"
            ),
            "enqueue failed",
        ),
        (lambda: mcp_mod.forget_conversation_memory("session-1"), "forget failed"),
        (mcp_mod.get_conversation_memory_status, "status failed"),
    ],
)
async def test_conversation_memory_operations_map_provider_failures(
    fake_kb, operation, message
):
    mcp_mod.set_conversation_memory_service(
        FakeConversationMemoryService(fail=True)
    )

    with pytest.raises(RuntimeError, match=message) as error:
        await operation()

    assert "secret" not in str(error.value)


async def test_standalone_engine_mcp_manages_conversation_runtime(monkeypatch):
    from config.schema import AppConfig
    from config.settings import settings

    class Runtime:
        def __init__(self):
            self.started = 0
            self.stopped = 0

        async def start(self):
            self.started += 1

        async def stop(self):
            self.stopped += 1

    runtime = Runtime()
    conversation_service = FakeConversationMemoryService()
    captured = {}

    async def init_db():
        captured["db"] = True

    monkeypatch.setattr(
        "config.schema.load_config",
        lambda: AppConfig.model_validate({"hindsight": {"enabled": True}}),
    )
    monkeypatch.setattr(
        "src.engine.components.store.postgres.init_db", init_db
    )
    monkeypatch.setattr(
        "src.engine.config.build_engine", lambda config: FakeKnowledgeBase()
    )
    monkeypatch.setattr(
        "src.engine.hindsight_components.query.build_query_service", lambda: object()
    )
    monkeypatch.setattr(
        "src.engine.hindsight_components.hook.build_retain_hook",
        lambda **kwargs: object(),
    )
    monkeypatch.setattr(
        "src.engine.hindsight_components.conversation_service.build_conversation_memory_service",
        lambda **kwargs: (
            captured.update(service_build=kwargs) or conversation_service
        ),
    )

    def build_runtime(**kwargs):
        captured["runtime_build"] = kwargs
        return runtime

    monkeypatch.setattr(
        "src.engine.hindsight_components.conversation_worker.build_conversation_worker_runtime",
        build_runtime,
    )
    monkeypatch.setattr(settings, "hindsight_graph_worker_enabled", False)
    monkeypatch.setattr(settings, "hindsight_conversation_memory_enabled", True)
    monkeypatch.setattr(settings, "hindsight_conversation_recall_limit", 6)
    monkeypatch.setattr(settings, "hindsight_conversation_worker_poll_seconds", 2.0)
    monkeypatch.setattr(settings, "hindsight_conversation_worker_lease_seconds", 60)
    monkeypatch.setattr(settings, "hindsight_conversation_worker_max_attempts", 4)
    monkeypatch.setattr(settings, "hindsight_conversation_worker_max_concurrent", 2)
    monkeypatch.setattr(settings, "hindsight_conversation_worker_retry_seconds", 3.0)
    monkeypatch.setattr(
        settings, "hindsight_conversation_worker_max_retry_seconds", 30.0
    )
    monkeypatch.setattr(
        settings, "hindsight_conversation_retention_context", "MCP chat turn"
    )
    mcp_mod._engine_worker_runtimes.clear()

    await mcp_mod.startup_engine_mcp()

    assert captured["db"] is True
    assert mcp_mod._conversation_memory_service is conversation_service
    assert captured["service_build"] == {"max_recall_results": 6}
    assert captured["runtime_build"]["max_concurrent"] == 2
    assert captured["runtime_build"]["retention_context"] == "MCP chat turn"
    assert runtime.started == 1

    await mcp_mod.shutdown_engine_mcp()
    assert runtime.stopped == 1
    assert mcp_mod._conversation_memory_service is None
    assert mcp_mod._engine_worker_runtimes == []


def test_private_memory_operations_are_registered_without_removing_existing_tools():
    tool_names = set(mcp_mod.mcp._tool_manager._tools)
    assert {
        "search",
        "query_knowledge",
        "upload_document",
        "list_documents",
    } <= tool_names
    assert {
        "recall_conversation_memory",
        "enqueue_conversation_turn",
        "forget_conversation_memory",
        "get_conversation_memory_status",
    } <= tool_names


async def test_original_search_tool_uses_hindsight_when_available(fake_kb):
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
                        memory_type="world",
                        doc_id="d1",
                        title="Doc",
                        chunk_text="context",
                    )
                ],
            )

    service = FakeQueryService()
    mcp_mod.set_query_service(service)

    out = await mcp_mod.search("分析项目进展", top_k=3, mode="deep", needs_answer=True)

    assert fake_kb.recall_calls == []
    assert service.request.mode == "deep"
    assert service.request.needs_answer is True
    assert out["answer"] == "answer"
    assert out["chunks"][0]["memory_id"] == "m1"


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


@pytest.mark.parametrize(
    ("tool", "expected_mode", "top_k"),
    [
        (mcp_mod.search_knowledge_fast, "fast", 4),
        (mcp_mod.search_knowledge_deep, "deep", 8),
    ],
)
async def test_search_tools_fix_recall_mode(tool, expected_mode, top_k):
    class FakeQueryService:
        request = None

        async def query(self, request):
            self.request = request
            return KnowledgeQueryResult(strategy_used="recall")

    service = FakeQueryService()
    mcp_mod.set_query_service(service)
    try:
        out = await tool("acme", top_k=top_k)
    finally:
        mcp_mod._query_service = None

    assert out["strategy_used"] == "recall"
    assert service.request.query == "acme"
    assert service.request.strategy == "recall"
    assert service.request.mode == expected_mode
    assert service.request.top_k == top_k
    assert service.request.needs_answer is False


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


async def test_upload_document_includes_memory_state_when_adapter_provides_it(
    fake_kb,
):
    original_ingest = fake_kb.ingest

    async def ingest(source):
        ref = await original_ingest(source)
        ref.memory_status = "pending"
        ref.memory_count = 0
        ref.memory_link_count = 0
        return ref

    fake_kb.ingest = ingest
    result = await mcp_mod.upload_document("week.md", "content")

    assert result["memory_status"] == "pending"
    assert result["memory_count"] == 0
    assert result["memory_link_count"] == 0


async def test_edit_document_content(fake_kb):
    uploaded = await fake_kb.ingest(IngestSource(name="week.md", data=b"old"))

    result = await mcp_mod.edit_document_content(uploaded.id, "new")

    assert result["status"] == "pending"
    assert fake_kb.raw[uploaded.id] == b"new"


async def test_reingest_document(fake_kb):
    uploaded = await fake_kb.ingest(IngestSource(name="week.md", data=b"content"))

    result = await mcp_mod.reingest_document(uploaded.id)

    assert result["status"] == "pending"


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


async def test_generate_document_tool_returns_download_link(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path))

    result = await mcp_mod.generate_document(
        "docx", "会议纪要", "# 决议\n\n- 发布新版本"
    )

    assert result["format"] == "docx"
    assert result["filename"].endswith(".docx")
    assert result["download_url"].startswith("/api/artifacts/")
