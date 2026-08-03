"""Uniform EngineClient over in-process and MCP transports.

InProcessEngineClient wraps a KnowledgeBase directly (used by the webapp BFF
when engine_access=inprocess). McpEngineClient calls the engine's MCP tools
over streamable HTTP (used by the codex harness and when engine_access=mcp).
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any, Literal

from src.engine.interface import KnowledgeBase, KnowledgeQuery


def _jsonable(obj: Any) -> Any:
    if hasattr(obj, "__dataclass_fields__"):
        return {k: _jsonable(v) for k, v in asdict(obj).items()}  # type: ignore[arg-type]
    if isinstance(obj, list):
        return [_jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    return obj


class InProcessEngineClient:
    """EngineClient backed by an in-process KnowledgeBase instance."""

    def __init__(
        self, kb: KnowledgeBase, query_service: KnowledgeQuery | None = None
    ) -> None:
        self._kb = kb
        self._query_service = query_service

    async def recall(
        self,
        query: str,
        top_k: int = 10,
        mode: Literal["auto", "fast", "deep"] = "auto",
        needs_answer: bool = False,
    ) -> dict:
        from src.engine.interface import RecallRequest

        request = RecallRequest(
            query=query,
            top_k=top_k,
            mode=mode,
            needs_answer=needs_answer,
        )
        if self._query_service is not None:
            from src.engine.hindsight_components.compat import HindsightRecallAdapter

            res = await HindsightRecallAdapter(self._query_service).recall(request)
        else:
            if mode != "auto" or needs_answer:
                raise RuntimeError("Hindsight 查询服务未初始化")
            res = await self._kb.recall(request)
        return _jsonable(res)

    async def query(
        self,
        query: str,
        strategy: Literal["auto", "recall", "reflect"] = "auto",
        mode: Literal["fast", "deep"] = "deep",
        top_k: int = 10,
        needs_answer: bool = True,
    ) -> dict:
        from src.engine.interface import KnowledgeQueryRequest

        if self._query_service is None:
            raise RuntimeError("Hindsight 查询服务未初始化")
        result = await self._query_service.query(
            KnowledgeQueryRequest(
                query=query,
                strategy=strategy,
                mode=mode,
                top_k=top_k,
                needs_answer=needs_answer,
            )
        )
        return _jsonable(result)

    async def ingest(self, name: str, data: bytes) -> dict:
        from src.engine.interface import IngestSource

        ref = await self._kb.ingest(IngestSource(name=name, data=data))
        return _jsonable(ref)

    async def get_document(self, doc_id: str) -> dict:
        out = await self._kb.get_document(doc_id)
        return out if out is not None else {"error": f"文档不存在: {doc_id}"}

    async def get_graph(self, entity: str | None = None) -> dict:
        return _jsonable(await self._kb.get_graph(entity))

    async def get_neighbors(self, entity: str) -> dict:
        return _jsonable(await self._kb.get_neighbors(entity))

    async def list_documents(
        self,
        page: int = 1,
        page_size: int = 20,
        file_type: str | None = None,
        status: str | None = None,
    ) -> dict:
        return await self._kb.list_documents(page, page_size, file_type, status)

    async def remove(self, doc_id: str) -> dict:
        await self._kb.remove(doc_id)
        return {"removed": doc_id}


class McpEngineClient:
    """EngineClient backed by an engine MCP server (streamable HTTP)."""

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url

    async def _call(self, tool: str, args: dict) -> dict:
        from mcp.client.session import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        async with streamablehttp_client(self._base_url) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool, args)
        text = result.content[0].text
        return json.loads(text)

    async def recall(
        self,
        query: str,
        top_k: int = 10,
        mode: Literal["auto", "fast", "deep"] = "auto",
        needs_answer: bool = False,
    ) -> dict:
        args: dict[str, Any] = {"query": query, "top_k": top_k}
        if mode != "auto":
            args["mode"] = mode
        if needs_answer:
            args["needs_answer"] = True
        return await self._call("search", args)

    async def query(
        self,
        query: str,
        strategy: Literal["auto", "recall", "reflect"] = "auto",
        mode: Literal["fast", "deep"] = "deep",
        top_k: int = 10,
        needs_answer: bool = True,
    ) -> dict:
        return await self._call(
            "query_knowledge",
            {
                "query": query,
                "strategy": strategy,
                "mode": mode,
                "top_k": top_k,
                "needs_answer": needs_answer,
            },
        )

    async def ingest(self, name: str, data: bytes) -> dict:
        return await self._call(
            "upload_document", {"file_name": name, "content": data.decode("utf-8")}
        )

    async def get_document(self, doc_id: str) -> dict:
        return await self._call("get_document", {"doc_id": doc_id})

    async def get_graph(self, entity: str | None = None) -> dict:
        if entity is None:
            return await self._call("get_full_graph", {})
        return await self._call(
            "query_graph", {"entity_name": entity, "include_neighbors": False}
        )

    async def get_neighbors(self, entity: str) -> dict:
        return await self._call(
            "query_graph", {"entity_name": entity, "include_neighbors": True, "hops": 2}
        )

    async def list_documents(
        self,
        page: int = 1,
        page_size: int = 20,
        file_type: str | None = None,
        status: str | None = None,
    ) -> dict:
        return await self._call(
            "list_documents",
            {
                "page": page,
                "page_size": page_size,
                "file_type": file_type,
                "status": status,
            },
        )

    async def remove(self, doc_id: str) -> dict:
        return await self._call("remove_document", {"doc_id": doc_id})
