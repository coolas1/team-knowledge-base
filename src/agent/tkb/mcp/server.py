"""MCP server wrapping a KnowledgeBase (the face external hosts consume).
Relocated from src/engine/mcp.py - MCP belongs to the plugin, not the engine
(docs/architecture.md §1, §2). remove_document is hook-guarded (policy-as-data).
When a KnowledgeQuery is wired (hindsight engine), additional recall/reflect
tools are registered."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from src.engine.trusted_scope import bind_service, resolve_binding

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from src.engine.interface import (
    ConversationForgetRequest,
    ConversationMemory,
    ConversationMemoryDiagnostics,
    ConversationMemoryRecallRequest,
    ConversationTurn,
    KnowledgeBase,
    KnowledgeQuery,
    KnowledgeQueryRequest,
    MemoryExpansionRequest,
)
from src.agent.policy import HookPolicy, NeedsApproval

mcp = FastMCP(
    "Team Knowledge Base",
    streamable_http_path="/",
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[
            "127.0.0.1:*",
            "localhost:*",
            "[::1]:*",
            # Single-app compose: in-network clients (pi-agent sidecar) reach
            # the backend by service name or container name.
            "backend:8000",
            "webapp:8000",
            "team-kb-webapp:8000",
        ],
        allowed_origins=[
            "http://127.0.0.1:*",
            "http://localhost:*",
            "http://[::1]:*",
        ],
    ),
)

_kb: KnowledgeBase | None = None
_query_service: KnowledgeQuery | None = None
_conversation_memory_service: ConversationMemory | None = None
# Default: load tkb hooks so the standalone MCP server is hook-guarded.
# The host overrides via set_hooks() with the selected plugin's policy.
_hooks = HookPolicy.load(Path(__file__).resolve().parent.parent / "hooks")


def set_kb(kb: KnowledgeBase) -> None:
    global _kb
    _kb = kb


def set_query_service(query_service: KnowledgeQuery | None) -> None:
    global _query_service
    _query_service = query_service
    if query_service is None:
        _unregister_memory_tools()
    else:
        _register_memory_tools()


def set_hooks(hooks: HookPolicy) -> None:
    global _hooks
    _hooks = hooks


def set_conversation_memory_service(
    conversation_memory_service: ConversationMemory | None,
) -> None:
    global _conversation_memory_service
    _conversation_memory_service = conversation_memory_service


def _get_kb() -> KnowledgeBase:
    if _kb is None:
        raise RuntimeError("KnowledgeBase 未初始化")
    return bind_service(_kb, _request_binding(), writes=True)


def _get_query_service() -> KnowledgeQuery:
    if _query_service is None:
        raise RuntimeError("Hindsight 查询服务未初始化")
    return bind_service(_query_service, _request_binding())


def _get_conversation_memory_service() -> ConversationMemory:
    if _conversation_memory_service is None:
        raise RuntimeError("Conversation memory is disabled")
    return bind_service(_conversation_memory_service, _request_binding(), writes=True)


def _request_binding():
    import os
    from config.schema import load_config
    from config.settings import settings

    try:
        request = mcp.get_context().request_context.request
    except ValueError:
        request = None  # stdio/direct in-process calls use the default scope.
    return resolve_binding(
        request.headers if request is not None else {},
        enabled=load_config(
            os.getenv("APP_CONFIG", "config/app.yaml")
        ).engine.memory.features.scope,
        bindings=settings.memory_scope_bindings,
    )


def _conversation_operation_failed(operation: str, error: Exception) -> RuntimeError:
    return RuntimeError(f"Conversation memory {operation} failed")


async def recall_conversation_memory(
    query: str,
    top_k: int = 5,
    mode: Literal["fast", "deep"] = "fast",
    memory_types: list[str] | None = None,
    include_source_time: bool = False,
) -> dict[str, Any]:
    """Internal runtime operation; not intended for model-selected tools."""
    if not query.strip():
        raise ValueError("query cannot be empty")
    if top_k < 1:
        raise ValueError("top_k must be greater than zero")
    if mode not in {"fast", "deep"}:
        raise ValueError(f"unsupported retrieval mode: {mode}")
    try:
        result = await _get_conversation_memory_service().recall_conversation_memory(
            ConversationMemoryRecallRequest(
                query=query,
                top_k=top_k,
                mode=mode,
                memory_types=tuple(memory_types or ()),
                include_source_time=include_source_time,
            )
        )
    except (ValueError, RuntimeError) as error:
        if isinstance(error, ValueError) or _conversation_memory_service is None:
            raise
        raise _conversation_operation_failed("recall", error) from error
    except Exception as error:
        raise _conversation_operation_failed("recall", error) from error
    return asdict(result)


async def enqueue_conversation_turn(
    session_id: str,
    turn_id: str,
    user_text: str,
    assistant_text: str,
    require_durable_acceptance: bool = False,
    source_timestamp: str | None = None,
    reference_timezone: str = "UTC",
) -> dict[str, Any]:
    """Internal runtime operation; not intended for model-selected tools."""
    if not session_id.strip() or not turn_id.strip():
        raise ValueError("session_id and turn_id must not be empty")
    if not user_text.strip() or not assistant_text.strip():
        raise ValueError("user_text and assistant_text must not be empty")
    if require_durable_acceptance:
        import os
        from config.schema import load_config

        if not load_config(
            os.getenv("APP_CONFIG", "config/app.yaml")
        ).engine.memory.features.reliable_retention:
            raise ValueError("reliable delivery is disabled")
    try:
        result = await _get_conversation_memory_service().enqueue_conversation_turn(
            ConversationTurn(
                session_id=session_id,
                turn_id=turn_id,
                user_text=user_text,
                assistant_text=assistant_text,
                source_timestamp=source_timestamp,
                reference_timezone=reference_timezone,
            )
        )
    except (ValueError, RuntimeError) as error:
        if isinstance(error, ValueError) or _conversation_memory_service is None:
            raise
        raise _conversation_operation_failed("enqueue", error) from error
    except Exception as error:
        raise _conversation_operation_failed("enqueue", error) from error
    response = asdict(result)
    operation_id = response.pop("operation_id", None)
    if require_durable_acceptance:
        import hashlib
        import json

        response.update(
            durable_acceptance=True,
            operation_id=operation_id or result.document_id,
            content_hash=hashlib.sha256(
                json.dumps(
                    [user_text, assistant_text],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest(),
        )
    return response


async def forget_conversation_memory(session_id: str) -> dict[str, Any]:
    """Internal runtime operation; not intended for model-selected tools."""
    if not session_id.strip():
        raise ValueError("session_id must not be empty")
    try:
        result = await _get_conversation_memory_service().forget_conversation_memory(
            ConversationForgetRequest(session_id=session_id)
        )
    except (ValueError, RuntimeError) as error:
        if isinstance(error, ValueError) or _conversation_memory_service is None:
            raise
        raise _conversation_operation_failed("forget", error) from error
    except Exception as error:
        raise _conversation_operation_failed("forget", error) from error
    return asdict(result)


async def retry_memory_extraction(document_id: str) -> dict[str, Any]:
    """Retry a failed conversation extraction in the caller's trusted scope."""
    import uuid

    try:
        document_id = str(uuid.UUID(document_id))
    except ValueError:
        raise ValueError("invalid document_id") from None
    service = _get_conversation_memory_service()
    try:
        scheduled = await service.retry_extraction(document_id)
    except Exception as error:
        raise _conversation_operation_failed("retry", error) from error
    return {"document_id": document_id, "scheduled": scheduled}


async def get_conversation_memory_status() -> dict[str, Any]:
    """Return aggregate queue state without retained conversation content."""
    if _conversation_memory_service is None:
        return asdict(ConversationMemoryDiagnostics(enabled=False))
    try:
        result = await _conversation_memory_service.conversation_memory_diagnostics()
    except Exception as error:
        raise _conversation_operation_failed("status", error) from error
    return asdict(result)


async def search(
    query: str,
    top_k: int = 20,
    mode: Literal["auto", "fast", "deep"] = "auto",
    needs_answer: bool = False,
) -> dict[str, Any]:
    """检索知识库。

    Hindsight 启用时使用其 recall/reflect：简单事实选择 fast，复杂比较、
    多跳和时间线选择 deep；需要服务端生成答案时设置 needs_answer=true。
    旧调用只传 query/top_k 仍兼容。Hindsight 未启用时回退到 GraphRAG。
    """
    from src.engine.interface import RecallRequest

    request = RecallRequest(
        query=query,
        top_k=top_k,
        mode=mode,
        needs_answer=needs_answer,
    )
    if _query_service is not None:
        from src.engine.hindsight_components.compat import HindsightRecallAdapter

        result = await HindsightRecallAdapter(_query_service).recall(request)
    else:
        # No memory capability wired: fall back to baseline recall; mode and
        # needs_answer are ignored (no server-side answer without reflect).
        result = await _get_kb().recall(RecallRequest(query=query, top_k=top_k))

    payload = asdict(result)
    for chunk in payload["chunks"]:
        chunk["chunk_text"] = chunk["chunk_text"][:1000]
    return payload


async def query_knowledge(
    query: str,
    strategy: Literal["auto", "recall", "reflect"] = "auto",
    mode: Literal["fast", "deep"] = "deep",
    top_k: int = 10,
    needs_answer: bool = True,
    correlation_id: str | None = None,
    memory_types: list[str] | None = None,
    source_types: list[str] | None = None,
    tags: list[str] | None = None,
    tags_match: str = "any",
    reference_time: str | None = None,
    min_scores: dict[str, float] | None = None,
    prefer_observations: bool = False,
    include: list[str] | None = None,
    include_stale: bool = False,
    timeout_seconds: float | None = None,
    max_tokens: int | None = None,
    max_candidates: int | None = None,
) -> dict[str, Any]:
    """通过 Hindsight 统一入口执行 recall 或 reflect。"""
    result = await _get_query_service().query(
        KnowledgeQueryRequest(
            query=query,
            strategy=strategy,
            mode=mode,
            top_k=top_k,
            needs_answer=needs_answer,
            correlation_id=correlation_id,
            memory_types=tuple(memory_types or ()),
            source_types=tuple(source_types or ()),
            tags=tuple(tags or ()),
            tags_match=tags_match,
            reference_time=(
                datetime.fromisoformat(reference_time.replace("Z", "+00:00"))
                if reference_time
                else None
            ),
            min_scores=dict(min_scores or {}),
            prefer_observations=prefer_observations,
            include=tuple(include) if include is not None else ("chunks", "entities"),
            include_stale=include_stale,
            timeout_seconds=timeout_seconds,
            max_tokens=max_tokens,
            max_candidates=max_candidates,
        )
    )
    return asdict(result)


async def expand_memory(
    memory_id: str,
    include: list[str] | None = None,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    """Expand a visible memory into bounded current source evidence."""
    result = await _get_query_service().expand_memory(
        MemoryExpansionRequest(
            memory_id=memory_id,
            include=tuple(include)
            if include is not None
            else ("chunk", "document", "source_facts"),
            max_tokens=max_tokens,
        )
    )
    return asdict(result) if result is not None else {"error": "memory not found"}


async def search_knowledge_fast(
    query: str,
    top_k: int = 5,
) -> dict[str, Any]:
    """快速知识检索。用于简单事实、定义、明确关键词、指定文件内容和文件定位。

    只返回检索证据，不在服务端生成最终答案；调用此工具的模型应根据 sources
    组织回答。不要用于跨文档比较、多跳关系、时间线或复杂综合分析。
    """
    return await query_knowledge(
        query,
        strategy="recall",
        mode="fast",
        top_k=top_k,
        needs_answer=False,
    )


async def search_knowledge_deep(
    query: str,
    top_k: int = 10,
    correlation_id: str | None = None,
) -> dict[str, Any]:
    """深度知识检索。用于跨文档比较、多跳关系、时间线、原因分析和综合总结。

    只返回检索证据，不在服务端生成最终答案；调用此工具的模型应综合 sources、
    related_entities 和 based_on 回答。简单事实查询应优先使用 search_knowledge_fast。
    """
    from src.engine.hindsight_components.errors import DeepSearchError

    try:
        return await query_knowledge(
            query,
            strategy="recall",
            mode="deep",
            top_k=top_k,
            needs_answer=False,
            correlation_id=correlation_id,
        )
    except DeepSearchError as error:
        return error.as_payload()


async def get_document(doc_id: str) -> dict[str, Any]:
    """获取文件详情。"""
    result = await _get_kb().get_document(doc_id)
    if not result:
        return {"error": f"文档不存在: {doc_id}"}
    return result


async def query_graph(
    entity_name: str, include_neighbors: bool = True, hops: int = 2
) -> dict[str, Any]:
    """查询知识图谱中的实体及其关系。"""
    kb = _get_kb()
    graph = await kb.get_graph(entity_name)
    if not graph.nodes:
        return {"error": f"实体不存在: {entity_name}"}
    node = graph.nodes[0]
    out: dict[str, Any] = {
        "name": node.name,
        "type": node.type,
        "properties": {"description": node.description, "sources": node.sources},
        "relations": [
            {
                "type": link.type,
                "other": link.target if link.source == node.name else link.source,
                "description": link.description,
            }
            for link in graph.links
        ],
    }
    if include_neighbors:
        neighbors = await kb.get_neighbors(entity_name)
        out["neighbors"] = [
            {"name": n.name, "type": n.type, "description": n.description}
            for n in neighbors.nodes
        ]
    return out


async def upload_document(file_name: str, content: str) -> dict[str, Any]:
    """上传文档到知识库（文本内容直接上传）。"""
    from src.engine.interface import IngestSource

    ref = await _get_kb().ingest(
        IngestSource(name=file_name, data=content.encode("utf-8"))
    )
    result = {
        "id": ref.id,
        "title": ref.title,
        "file_type": ref.file_type,
        "status": ref.status,
    }
    if ref.memory_status is not None:
        result.update(
            {
                "memory_status": ref.memory_status,
                "memory_error_msg": ref.memory_error_msg,
                "memory_count": ref.memory_count,
                "memory_link_count": ref.memory_link_count,
            }
        )
    return result


async def edit_document_content(doc_id: str, content: str) -> dict[str, Any]:
    """保存文档正文并在后台重建索引。"""
    return asdict(await _get_kb().edit_content(doc_id, content))


async def reingest_document(doc_id: str) -> dict[str, Any]:
    """重新处理失败文档，并在后台重建索引。"""
    return asdict(await _get_kb().reingest(doc_id))


async def list_documents(
    page: int = 1,
    page_size: int = 20,
    file_type: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    """文件列表（分页，按 type/status 筛选）。"""
    return await _get_kb().list_documents(page, page_size, file_type, status)


async def remove_document(doc_id: str, approved: bool = False) -> dict[str, Any]:
    """删除文件（级联删 chunks + Neo4j + 本地文件）。需审批（policy-as-data）。"""
    decision = _hooks.check("remove", {"doc_id": doc_id}, approved=approved)
    if isinstance(decision, NeedsApproval):
        return {
            "status": decision.status,
            "pending_action": {"op": "remove", "params": {"doc_id": doc_id}},
            "question": decision.question,
        }
    await _get_kb().remove(doc_id)
    return {"removed": doc_id}


async def get_full_graph() -> dict[str, Any]:
    """返回全图数据（所有实体 + 关系）。"""
    graph = await _get_kb().get_graph(None)
    return {
        "nodes": [
            {
                "name": n.name,
                "type": n.type,
                "description": n.description,
                "sources": n.sources,
            }
            for n in graph.nodes
        ],
        "links": [
            {
                "source": link.source,
                "target": link.target,
                "type": link.type,
                "description": link.description,
            }
            for link in graph.links
        ],
    }


async def generate_document(
    format: Literal["docx", "pdf", "pptx"],
    title: str,
    content: str,
    file_name: str | None = None,
) -> dict[str, Any]:
    """生成可下载的 Word、PDF 或 PPT 文档。

    content 使用 Markdown。生成 PPT 时以独占一行的 ``---`` 分隔幻灯片，
    每页首个 Markdown 标题作为页标题；同时返回 PPTX 和 Slidev 源文件链接。
    """
    from src.agent.artifacts import generate_artifact

    binding = _request_binding()

    return asdict(
        generate_artifact(
            format=format,
            title=title,
            content=content,
            file_name=file_name,
            scope=binding.scope(),
            write_tags=binding.write_tags,
        )
    )


# Register tools (FastMCP introspects signatures). The three memory tools are
# registered only when a query service is wired (see set_query_service).
_MEMORY_TOOL_NAMES = (
    "query_knowledge",
    "search_knowledge_fast",
    "search_knowledge_deep",
    "expand_memory",
)

_memory_tools_registered = False


def _register_memory_tools() -> None:
    """Register recall/reflect tools iff a query service is wired (idempotent)."""
    global _memory_tools_registered
    if _memory_tools_registered or _query_service is None:
        return
    mcp.tool()(query_knowledge)
    mcp.tool()(search_knowledge_fast)
    mcp.tool()(search_knowledge_deep)
    mcp.tool()(expand_memory)
    _memory_tools_registered = True


def _unregister_memory_tools() -> None:
    global _memory_tools_registered
    if not _memory_tools_registered:
        return
    for name in _MEMORY_TOOL_NAMES:
        try:
            mcp.remove_tool(name)
        except Exception:
            pass  # never registered; nothing to remove
    _memory_tools_registered = False


mcp.tool()(search)
mcp.tool()(get_document)
mcp.tool()(query_graph)
mcp.tool()(upload_document)
mcp.tool()(edit_document_content)
mcp.tool()(reingest_document)
mcp.tool()(list_documents)
mcp.tool()(remove_document)
mcp.tool()(get_full_graph)
mcp.tool()(generate_document)
mcp.tool()(recall_conversation_memory)
mcp.tool()(enqueue_conversation_turn)
mcp.tool()(forget_conversation_memory)
mcp.tool()(get_conversation_memory_status)
mcp.tool()(retry_memory_extraction)


def build_app():
    """Return the streamable-HTTP ASGI app; caller manages MCP sessions."""
    return mcp.streamable_http_app()
