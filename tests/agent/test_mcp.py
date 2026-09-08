import pytest
from mcp.server.transport_security import TransportSecurityMiddleware

from src.agent.tkb.mcp import server as mcp_mod
from src.engine.interface import (
    ConversationEnqueueResult,
    ConversationForgetResult,
    ConversationMemoryDiagnostics,
    ConversationMemoryItem,
    ConversationMemoryRecallResult,
    DocumentRef,
    GraphData,
    GraphNode,
    IngestSource,
    KnowledgeQueryResult,
    KnowledgeSource,
    RecallResult,
)
from tests.conftest import FakeKnowledgeBase


def test_mcp_transport_allows_localhost_and_compose_service_hosts():
    settings = mcp_mod.mcp.settings.transport_security
    assert settings is not None
    assert settings.enable_dns_rebinding_protection is True

    security = TransportSecurityMiddleware(settings)
    # localhost clients, on any port
    for host in ("localhost:8000", "127.0.0.1:8000", "[::1]:8000", "localhost:1234"):
        assert security._validate_host(host) is True, host
    # single-app compose: pi-agent reaches the backend by service/container name
    for host in ("backend:8000", "webapp:8000", "team-kb-webapp:8000"):
        assert security._validate_host(host) is True, host


def test_mcp_transport_rejects_unknown_hosts():
    security = TransportSecurityMiddleware(mcp_mod.mcp.settings.transport_security)
    # DNS-rebind protection stays active for anything outside the allowlist
    assert security._validate_host("attacker.example:8000") is False
    # near-misses must not be accepted either
    assert security._validate_host("webapp.evil.example:8000") is False
    assert security._validate_host("webapp:8001") is False


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

    async def retry_extraction(self, document_id):
        self.calls.append(("retry", document_id))
        if self.fail:
            raise RuntimeError("repository secret")
        return True


async def test_extraction_retry_reports_scheduling_and_sanitizes_failure(fake_kb):
    document_id = "00000000-0000-0000-0000-000000000001"
    service = FakeConversationMemoryService()
    mcp_mod.set_conversation_memory_service(service)
    assert await mcp_mod.retry_memory_extraction(document_id) == {
        "document_id": document_id,
        "scheduled": True,
    }
    assert service.calls == [("retry", document_id)]
    service.fail = True
    with pytest.raises(RuntimeError) as caught:
        await mcp_mod.retry_memory_extraction(document_id)
    assert "secret" not in str(caught.value)
    with pytest.raises(ValueError, match="invalid document_id"):
        await mcp_mod.retry_memory_extraction("bad-id")


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


async def test_conversation_delivery_acknowledges_committed_content(
    fake_kb, monkeypatch
):
    import hashlib
    import json
    from config import schema

    monkeypatch.setattr(schema, "load_config", lambda *_args: schema.AppConfig())
    with pytest.raises(ValueError, match="reliable delivery is disabled"):
        await mcp_mod.enqueue_conversation_turn(
            "session-1", "turn-1", "question", "answer", require_durable_acceptance=True
        )
    configured = schema.AppConfig.model_validate(
        {
            "engine": {
                "memory": {
                    "enabled": True,
                    "features": {"scope": True, "reliable_retention": True},
                }
            }
        }
    )
    monkeypatch.setattr(schema, "load_config", lambda *_args: configured)

    service = FakeConversationMemoryService()
    mcp_mod.set_conversation_memory_service(service)
    result = await mcp_mod.enqueue_conversation_turn(
        "session-1", "turn-1", " 问题\n", "answer", require_durable_acceptance=True
    )
    assert result["durable_acceptance"] is True
    assert (
        result["content_hash"]
        == hashlib.sha256(
            json.dumps(
                [" 问题\n", "answer"], ensure_ascii=False, separators=(",", ":")
            ).encode()
        ).hexdigest()
    )
    service.fail = True
    with pytest.raises(RuntimeError, match="enqueue failed"):
        await mcp_mod.enqueue_conversation_turn(
            "session-1", "turn-1", " 问题\n", "answer", require_durable_acceptance=True
        )


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
    mcp_mod.set_conversation_memory_service(FakeConversationMemoryService(fail=True))

    with pytest.raises(RuntimeError, match=message) as error:
        await operation()

    assert "secret" not in str(error.value)


def test_private_memory_operations_are_registered_without_removing_existing_tools():
    tool_names = set(mcp_mod.mcp._tool_manager._tools)
    assert {
        "search",
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


async def test_deep_search_returns_typed_timeout_payload():
    from src.engine.hindsight_components.errors import DeepSearchTimeoutError

    class FakeQueryService:
        async def query(self, request):
            raise DeepSearchTimeoutError(
                request.correlation_id or "missing",
                {
                    "search_id": request.correlation_id,
                    "outcome": "deep_search_timeout",
                    "phase_outcomes": {},
                },
            )

    mcp_mod.set_query_service(FakeQueryService())
    try:
        out = await mcp_mod.search_knowledge_deep(
            "compare", correlation_id="pi-correlation"
        )
    finally:
        mcp_mod._query_service = None

    assert out["error"] == {
        "code": "deep_search_timeout",
        "search_id": "pi-correlation",
    }


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


async def test_remove_document_unapproved_needs_approval(fake_kb):
    fake_kb.docs["abc"] = DocumentRef(
        id="abc", title="t", file_type="markdown", status="indexed"
    )
    res = await mcp_mod.remove_document("abc")
    assert res["status"] == "needs_approval"
    assert res["pending_action"] == {"op": "remove", "params": {"doc_id": "abc"}}
    assert "abc" in fake_kb.docs  # not deleted


async def test_remove_document_approved_executes(fake_kb):
    fake_kb.docs["abc"] = DocumentRef(
        id="abc", title="t", file_type="markdown", status="indexed"
    )
    res = await mcp_mod.remove_document("abc", approved=True)
    assert res == {"removed": "abc"}
    assert "abc" not in fake_kb.docs


async def test_get_full_graph_tool(fake_kb):
    res = await mcp_mod.get_full_graph()
    assert res == {"nodes": [], "links": []}


async def test_memory_tools_absent_without_query_service():
    class FakeKB:
        capabilities = None

        async def recall(self, request):
            return RecallResult()

    mcp_mod.set_kb(FakeKB())
    mcp_mod.set_query_service(None)
    try:
        tools = {t.name for t in await mcp_mod.mcp.list_tools()}
        assert "query_knowledge" not in tools
        assert "search_knowledge_fast" not in tools
        assert "search_knowledge_deep" not in tools
        assert "search" in tools  # baseline tool always present
    finally:
        mcp_mod._kb = None


async def test_memory_tools_present_with_query_service():
    class FakeQS:
        async def query(self, request):
            return KnowledgeQueryResult(strategy_used="recall")

    mcp_mod.set_query_service(FakeQS())
    try:
        tools = {t.name for t in await mcp_mod.mcp.list_tools()}
        assert {
            "query_knowledge",
            "search_knowledge_fast",
            "search_knowledge_deep",
        } <= tools
    finally:
        mcp_mod.set_query_service(None)
        mcp_mod._kb = None


async def test_search_falls_back_without_query_service():
    calls = {}

    class FakeKB:
        capabilities = None

        async def recall(self, request):
            calls["mode"] = request.mode
            return RecallResult()

    mcp_mod.set_kb(FakeKB())
    mcp_mod.set_query_service(None)
    try:
        payload = await mcp_mod.search("q", top_k=5, mode="deep", needs_answer=True)
    finally:
        mcp_mod._kb = None
    assert calls["mode"] == "auto"  # ignored, fell back to baseline recall
    assert payload["answer"] is None


async def test_generate_document_tool_returns_download_link(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path))

    result = await mcp_mod.generate_document(
        "docx", "会议纪要", "# 决议\n\n- 发布新版本"
    )

    assert result["format"] == "docx"
    assert result["filename"].endswith(".docx")
    assert result["download_url"].startswith("/api/artifacts/")
