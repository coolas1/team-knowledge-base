"""Run the plugin MCP server: uv run python -m src.agent.tkb.mcp  (port 8000, /mcp)."""
import uvicorn

from src.agent.tkb.mcp.server import build_app


def main() -> None:
    uvicorn.run(build_app(), host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
