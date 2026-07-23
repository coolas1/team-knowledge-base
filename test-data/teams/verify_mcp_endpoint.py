"""Verify MCP tool discovery and trusted team context through HTTP transport."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlencode


ROOT = Path(__file__).resolve().parents[2]
SITE_PACKAGES = ROOT / ".venv" / "Lib" / "site-packages"
for path in (SITE_PACKAGES, SITE_PACKAGES / "win32", SITE_PACKAGES / "win32" / "lib", SITE_PACKAGES / "pythonwin", ROOT):
    sys.path.insert(0, str(path))
os.add_dll_directory(str(SITE_PACKAGES / "pywin32_system32"))

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def main() -> None:
    token = os.environ.get("TKB_MCP_TOKEN")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    url = "http://127.0.0.1:8001/mcp"
    if username := os.environ.get("TKB_OLLAMA_USERNAME"):
        params = {"ollama_user": username}
        if team_id := os.environ.get("TKB_TEAM_ID"):
            params["team_id"] = team_id
        url = f"{url}?{urlencode(params)}"
    async with streamablehttp_client(
        url, headers=headers
    ) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            context = await session.call_tool("get_current_context", {})
            knowledge_bases = await session.call_tool("list_knowledge_bases", {})
            tool_team_id = os.environ.get("TKB_TOOL_TEAM_ID")
            search_result = None
            if search_query := os.environ.get("TKB_SEARCH_QUERY"):
                args = {"query": search_query}
                if tool_team_id:
                    args["knowledge_base_id"] = tool_team_id
                result = await session.call_tool("search", args)
                search_result = [item.model_dump() for item in result.content]
            graph_result = None
            if graph_entity := os.environ.get("TKB_GRAPH_ENTITY"):
                args = {"entity_name": graph_entity}
                if tool_team_id:
                    args["knowledge_base_id"] = tool_team_id
                result = await session.call_tool("query_graph", args)
                graph_result = [item.model_dump() for item in result.content]
            public_document = None
            if public_doc_id := os.environ.get("TKB_PUBLIC_DOC_ID"):
                args = {"doc_id": public_doc_id}
                if tool_team_id:
                    args["knowledge_base_id"] = tool_team_id
                public_result = await session.call_tool("get_document", args)
                public_document = [item.model_dump() for item in public_result.content]
            write_check = None
            if os.environ.get("TKB_EXPECT_READ_ONLY") == "1":
                write_result = await session.call_tool("upload_document", {
                    "file_name": "viewer-must-not-write.md",
                    "content": "This document must never be stored.",
                })
                write_check = {
                    "is_error": write_result.isError,
                    "content": [item.model_dump() for item in write_result.content],
                }
            print(json.dumps({
                "tools": [tool.name for tool in tools.tools],
                "context": [item.model_dump() for item in context.content],
                "knowledge_bases": [item.model_dump() for item in knowledge_bases.content],
                "search_result": search_result,
                "graph_result": graph_result,
                "public_document": public_document,
                "write_check": write_check,
            }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
