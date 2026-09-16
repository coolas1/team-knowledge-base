"""inbox 扫描器：轮询发现稳定文件并入队。

职责（对应 spec auto-archiving "stability and unseen"）：
- 过滤临时/隐藏文件（.part/.crdownload/.tmp/~ 前缀/隐藏文件）
- 稳定性：size+mtime 连续 N 次轮询不变（状态在内存，重启后重新累计）
- sha256 去重：已存在同 hash 的 job 时不再入队
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from .jobs import ArchiveJobQueue
from .hashing import file_sha256

logger = logging.getLogger(__name__)

# 下载器常见的临时后缀；命中即忽略。
TEMP_SUFFIXES = (".part", ".crdownload", ".download", ".tmp", ".partial")


def is_temp_file(path: Path) -> bool:
    """隐藏文件与临时下载文件不进流水线。"""
    name = path.name
    return name.startswith(".") or name.startswith("~") or (
        path.suffix.lower() in TEMP_SUFFIXES
    )


def file_fingerprint(path: Path) -> tuple[int, float] | None:
    """(size, mtime) 指纹；文件消失返回 None。"""
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_size, stat.st_mtime)


def content_hash(path: Path) -> str:
    """Compatibility wrapper for synchronous callers and unit tests."""
    return file_sha256(path)


@dataclass
class ScanResult:
    enqueued: list[str]
    skipped_temp: int = 0
    skipped_duplicate: int = 0
    pending_stability: int = 0


class InboxScanner:
    def __init__(
        self,
        inbox_dir: Path,
        queue: ArchiveJobQueue,
        *,
        stability_checks: int = 2,
    ) -> None:
        if stability_checks < 1:
            raise ValueError("stability_checks must be >= 1")
        self._inbox = Path(inbox_dir)
        self._queue = queue
        self._stability_checks = stability_checks
        # path -> (fingerprint, 连续不变次数, 已入队的 hash 缓存)
        self._stability: dict[str, tuple[tuple[int, float], int]] = {}

    async def scan(self) -> ScanResult:
        """一次扫描：发现候选 -> 稳定性判定 -> 去重 -> 入队。"""
        result = ScanResult(enqueued=[])
        if not self._inbox.is_dir():
            return result

        seen: set[str] = set()
        for entry in sorted(self._inbox.iterdir()):
            if not entry.is_file():
                continue
            key = str(entry)
            seen.add(key)
            if is_temp_file(entry):
                self._stability.pop(key, None)
                result.skipped_temp += 1
                continue

            fingerprint = file_fingerprint(entry)
            if fingerprint is None:  # 消失/不可读
                self._stability.pop(key, None)
                continue

            prev = self._stability.get(key)
            if prev is not None and prev[0] == fingerprint:
                count = prev[1] + 1
            else:
                count = 1
            self._stability[key] = (fingerprint, count)

            if count < self._stability_checks:
                result.pending_stability += 1
                continue

            digest = await asyncio.to_thread(content_hash, entry)
            enqueued = await self._enqueue(entry, digest)
            if enqueued is None:
                result.skipped_duplicate += 1
            else:
                result.enqueued.append(entry.name)
            # 无论入队与否，稳定且已处理的文件不再重复判定。
            self._stability.pop(key, None)

        # 清理已消失文件的稳定性状态。
        for key in list(self._stability):
            if key not in seen:
                del self._stability[key]
        return result

    async def _enqueue(self, entry: Path, digest: str) -> object | None:
        try:
            return await self._queue.enqueue(
                file_name=entry.name,
                file_path=str(entry.resolve()),
                content_hash=digest,
            )
        except Exception:
            logger.exception("入队失败: %s", entry)
            return None
