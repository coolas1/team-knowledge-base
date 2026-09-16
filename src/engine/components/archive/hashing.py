"""Non-blocking helpers for hashing archive files."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path


def file_sha256(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    """Hash a file incrementally without loading it wholly into memory."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


async def file_sha256_async(path: Path) -> str:
    """Run file hashing outside the event loop."""
    return await asyncio.to_thread(file_sha256, Path(path))
