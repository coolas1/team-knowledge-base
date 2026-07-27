"""Uniform EngineClient over in-process and MCP transports.

InProcessEngineClient wraps a KnowledgeBase directly (used by the webapp BFF
when engine_access=inprocess). McpEngineClient calls the engine's MCP tools
over streamable HTTP (used by the codex harness and when engine_access=mcp).
"""
from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from src.engine.interface import KnowledgeBase


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

    def __init__(self, kb: KnowledgeBase) -> None:
        self._kb = kb

    async def recall(self, query: str, top_k: int = 10) -> dict:
        from src.engine.interface import RecallRequest

        res = await self._kb.recall(RecallRequest(query=query, top_k=top_k))
        return _jsonable(res)

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

    async def recall(self, query: str, top_k: int = 10) -> dict:
        return await self._call("search", {"query": query})

    async def ingest(self, name: str, data: bytes) -> dict:
        return await self._call("upload_document", {"file_name": name, "content": data.decode("utf-8")})

    async def get_document(self, doc_id: str) -> dict:
        return await self._call("get_document", {"doc_id": doc_id})

    async def get_graph(self, entity: str | None = None) -> dict:
        return await self._call("query_graph", {"entity_name": entity or "", "include_neighbors": False})

    async def get_neighbors(self, entity: str) -> dict:
        return await self._call("query_graph", {"entity_name": entity, "include_neighbors": True, "hops": 2})
