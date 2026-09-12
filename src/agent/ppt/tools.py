"""MCP tools: persistent submission/status and explicit review links."""

import base64

from mcp.types import ImageContent

from .contracts import DeckSpec
from .provider import validate_image
from .runtime import authority, get_store


def register(mcp, request_binding):
    def context():
        binding = request_binding()
        try:
            request = mcp.get_context().request_context.request
        except ValueError:
            request = None
        return binding, authority(request.headers if request else {})

    @mcp.tool()
    async def create_ppt(spec: DeckSpec, session_id: str = "agent") -> dict:
        """Create an image-slide deck draft. Return its review link; generation requires user UI approval."""
        binding, key = context()
        identifier = await get_store().create(spec, binding, key, session_id)
        return {
            "id": identifier,
            "review_url": f"/ppt/{identifier}",
            "status": "awaiting_outline_approval",
        }

    @mcp.tool()
    async def get_ppt(identifier: str) -> dict:
        """Read persistent deck status, approved outline, page progress and usage."""
        binding, key = context()
        return await get_store().get(identifier, binding, key)

    @mcp.tool()
    async def approve_ppt(identifier: str, revision: int) -> dict:
        """Present a review link. Only the user's explicit UI action can approve this revision."""
        binding, key = context()
        state = await get_store().get(identifier, binding, key)
        if state["revision"] != revision:
            raise ValueError("Stale PPT revision")
        return {
            "requires_ui_approval": True,
            "review_url": f"/ppt/{identifier}",
            "revision": revision,
        }

    @mcp.tool()
    async def cancel_ppt(identifier: str, revision: int) -> dict:
        """Cancel further page generation when requested by the user."""
        binding, key = context()
        await get_store().control(identifier, binding, key, revision, "cancel")
        return {"status": "cancelled"}

    @mcp.tool()
    async def retry_ppt(identifier: str, revision: int, page: int) -> dict:
        """Link to explicit paid retry controls. Unknown outcomes may already have been charged."""
        binding, key = context()
        state = await get_store().get(identifier, binding, key)
        if state["revision"] != revision or not 1 <= page <= len(state["pages"]):
            raise ValueError("Stale or invalid page")
        return {
            "requires_ui_approval": True,
            "review_url": f"/ppt/{identifier}",
            "page": page,
        }

    @mcp.tool(structured_output=False)
    async def preview_ppt(identifier: str, page: int) -> list[ImageContent]:
        """Read the actual generated slide image for visual inspection."""
        binding, key = context()
        path = await get_store().file(identifier, binding, key, page=page)
        data = path.read_bytes()
        return [
            ImageContent(
                type="image",
                data=base64.b64encode(data).decode(),
                mimeType=validate_image(data),
            )
        ]
