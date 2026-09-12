"""Shared service accessor for HTTP/MCP and worker lifecycle."""

import hashlib
from pathlib import Path

from src.agent.artifacts import artifacts_root
from .store import PPTStore


def authority(headers) -> str:
    token = headers.get("x-tkb-scope-token")
    return (
        hashlib.sha256(token.encode()).hexdigest() if token is not None else "default"
    )


def get_store():
    from src.engine.components.store.postgres import async_session_factory

    return PPTStore(async_session_factory, Path(artifacts_root()) / "ppt")
