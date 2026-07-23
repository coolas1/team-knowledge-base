"""Directly verify the server-owned MCP team context."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SITE_PACKAGES = ROOT / ".venv" / "Lib" / "site-packages"
for path in (SITE_PACKAGES, SITE_PACKAGES / "win32", SITE_PACKAGES / "win32" / "lib", SITE_PACKAGES / "pythonwin", ROOT):
    sys.path.insert(0, str(path))
os.add_dll_directory(str(SITE_PACKAGES / "pywin32_system32"))

from src.api.mcp_server import get_current_context, list_knowledge_bases


async def main() -> None:
    context = await get_current_context()
    knowledge_bases = await list_knowledge_bases()
    print(json.dumps({"context": context, "knowledge_bases": knowledge_bases}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
