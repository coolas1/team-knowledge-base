"""MCP entry point for chat-native image presentation generation."""

from .contracts import DeckSpec
from .generator import generate_image_ppt


def register(mcp, request_binding):
    async def generate_image_ppt_tool(
        spec: DeckSpec,
        file_name: str | None = None,
        max_image_attempts: int | None = None,
    ) -> dict:
        """Generate a verified image-slide PPTX and return a normal artifact.

        This is a bounded foreground operation for the current chat turn. It
        reads only sources allowed by the request binding and does not create a
        persistent PPT job or hidden background retry.
        """
        return await generate_image_ppt(
            spec,
            request_binding(),
            file_name=file_name,
            max_image_attempts=max_image_attempts,
        )

    generate_image_ppt_tool.__name__ = "generate_image_ppt"
    mcp.tool()(generate_image_ppt_tool)
