"""Shared service accessor for HTTP/MCP and worker lifecycle."""

import hashlib
from pathlib import Path

from src.agent.artifacts import artifacts_root
from .store import PPTStore

_tasks = []


async def start():
    import asyncio
    from config.settings import settings

    if not settings.ppt.enabled:
        return
    settings.image.require_ready()
    from .assembly import Assembler
    from .quality import VisualReviewer
    from .provider import SeedreamProvider
    from .worker import PPTWorker

    store = get_store()
    for _ in range(settings.ppt.concurrency):
        worker = PPTWorker(
            store,
            SeedreamProvider(settings.image),
            VisualReviewer(store),
            Assembler(store),
        )
        _tasks.append(asyncio.create_task(worker.run(), name="ppt-worker"))


async def stop():
    import asyncio

    for task in _tasks:
        task.cancel()
    await asyncio.gather(*_tasks, return_exceptions=True)
    _tasks.clear()


def authority(headers) -> str:
    token = headers.get("x-tkb-scope-token")
    return (
        hashlib.sha256(token.encode()).hexdigest() if token is not None else "default"
    )


def get_store():
    from src.engine.components.store.postgres import async_session_factory

    return PPTStore(async_session_factory, Path(artifacts_root()) / "ppt")
