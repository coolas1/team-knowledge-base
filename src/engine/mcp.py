"""Thin MCP adapter: exposes a KnowledgeBase instance as MCP tools over
streamable HTTP. No business logic - each tool wraps one KnowledgeBase method.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal

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
)

mcp = FastMCP(
    "Team Knowledge Base",
    streamable_http_path="/",
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[
            "127.0.0.1:*",
            "localhost:*",
            "[::1]:*",
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
_engine_worker_runtimes: list[Any] = []


def set_kb(kb: KnowledgeBase) -> None:
    global _kb
    _kb = kb


def set_query_service(query_service: KnowledgeQuery | None) -> None:
    global _query_service
    _query_service = query_service


def set_conversation_memory_service(
    conversation_memory_service: ConversationMemory | None,
) -> None:
    global _conversation_memory_service
    _conversation_memory_service = conversation_memory_service


def _get_kb() -> KnowledgeBase:
    if _kb is None:
        raise RuntimeError("KnowledgeBase 未初始化")
    return _kb


def _get_query_service() -> KnowledgeQuery:
    if _query_service is None:
        raise RuntimeError("Hindsight 查询服务未初始化")
    return _query_service


def _get_conversation_memory_service() -> ConversationMemory:
    if _conversation_memory_service is None:
        raise RuntimeError("Conversation memory is disabled")
    return _conversation_memory_service


def _conversation_operation_failed(operation: str, error: Exception) -> RuntimeError:
    return RuntimeError(f"Conversation memory {operation} failed")


async def recall_conversation_memory(
    query: str,
    top_k: int = 5,
    mode: Literal["fast", "deep"] = "fast",
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
            ConversationMemoryRecallRequest(query=query, top_k=top_k, mode=mode)
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
) -> dict[str, Any]:
    """Internal runtime operation; not intended for model-selected tools."""
    if not session_id.strip() or not turn_id.strip():
        raise ValueError("session_id and turn_id must not be empty")
    if not user_text.strip() or not assistant_text.strip():
        raise ValueError("user_text and assistant_text must not be empty")
    try:
        result = await _get_conversation_memory_service().enqueue_conversation_turn(
            ConversationTurn(
                session_id=session_id,
                turn_id=turn_id,
                user_text=user_text,
                assistant_text=assistant_text,
            )
        )
    except (ValueError, RuntimeError) as error:
        if isinstance(error, ValueError) or _conversation_memory_service is None:
            raise
        raise _conversation_operation_failed("enqueue", error) from error
    except Exception as error:
        raise _conversation_operation_failed("enqueue", error) from error
    return asdict(result)


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
        if mode != "auto" or needs_answer:
            raise RuntimeError("Hindsight 查询服务未初始化")
        result = await _get_kb().recall(request)

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
        )
    )
    return asdict(result)


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


async def remove_document(doc_id: str) -> dict[str, Any]:
    """删除文件（级联删 chunks + Neo4j + 本地文件）。"""
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

    return asdict(
        generate_artifact(
            format=format,
            title=title,
            content=content,
            file_name=file_name,
        )
    )


# Register the async functions as MCP tools (FastMCP introspects signatures).
mcp.tool()(search)
mcp.tool()(query_knowledge)
mcp.tool()(search_knowledge_fast)
mcp.tool()(search_knowledge_deep)
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


def build_app():
    """Return the streamable-HTTP ASGI app; caller manages MCP sessions."""
    return mcp.streamable_http_app()


async def startup_engine_mcp() -> None:
    """Initialize the standalone engine MCP process and its workers."""
    from config.schema import load_config
    from config.settings import settings
    from src.engine.components.store.postgres import init_db
    from src.engine.config import EngineConfig, build_engine

    await init_db()
    cfg = load_config()
    query_service = None
    index_hook = None
    if cfg.hindsight.enabled:
        from src.engine.hindsight_components.adapter import (
            build_knowledge_base_adapter,
        )
        from src.engine.hindsight_components.hook import build_retain_hook
        from src.engine.hindsight_components.query import build_query_service

        query_service = build_query_service()
        index_hook = build_retain_hook(
            max_concurrent=cfg.hindsight.retain_max_concurrent
        )
    kb = build_engine(
        EngineConfig(
            impl=cfg.engine.impl,
            config_dir=Path(cfg.engine.config),
            index_hook=index_hook,
        )
    )
    if cfg.hindsight.enabled:
        kb = build_knowledge_base_adapter(kb)
    set_kb(kb)
    set_query_service(query_service)
    if cfg.hindsight.enabled and settings.hindsight_conversation_memory_enabled:
        from src.engine.hindsight_components.conversation_service import (
            build_conversation_memory_service,
        )
        from src.engine.hindsight_components.conversation_worker import (
            build_conversation_worker_runtime,
        )

        set_conversation_memory_service(
            build_conversation_memory_service(
                max_recall_results=settings.hindsight_conversation_recall_limit
            )
        )
        _engine_worker_runtimes.append(
            build_conversation_worker_runtime(
                poll_seconds=settings.hindsight_conversation_worker_poll_seconds,
                max_concurrent=(settings.hindsight_conversation_worker_max_concurrent),
                lease_seconds=settings.hindsight_conversation_worker_lease_seconds,
                max_attempts=settings.hindsight_conversation_worker_max_attempts,
                retry_delay_seconds=(
                    settings.hindsight_conversation_worker_retry_seconds
                ),
                max_retry_delay_seconds=(
                    settings.hindsight_conversation_worker_max_retry_seconds
                ),
                retention_context=(settings.hindsight_conversation_retention_context),
            )
        )
    else:
        set_conversation_memory_service(None)
    if cfg.hindsight.enabled and settings.hindsight_graph_worker_enabled:
        from src.engine.hindsight_components.graph_runtime import (
            build_graph_worker_runtime,
        )

        _engine_worker_runtimes.append(
            build_graph_worker_runtime(
                poll_seconds=settings.hindsight_graph_worker_poll_seconds,
                lease_seconds=settings.hindsight_graph_worker_lease_seconds,
                max_attempts=settings.hindsight_graph_worker_max_attempts,
            )
        )
    try:
        for runtime in _engine_worker_runtimes:
            await runtime.start()
    except Exception:
        await shutdown_engine_mcp()
        raise


async def shutdown_engine_mcp() -> None:
    """Stop standalone workers and clear process-owned services."""
    global _kb
    try:
        for runtime in reversed(_engine_worker_runtimes):
            await runtime.stop()
    finally:
        _engine_worker_runtimes.clear()
        set_conversation_memory_service(None)
        set_query_service(None)
        _kb = None


async def _serve_engine_mcp() -> None:
    await startup_engine_mcp()
    try:
        await mcp.run_streamable_http_async()
    finally:
        await shutdown_engine_mcp()


def main() -> None:
    asyncio.run(_serve_engine_mcp())


if __name__ == "__main__":
    main()
