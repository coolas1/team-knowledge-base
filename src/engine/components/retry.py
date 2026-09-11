"""Bounded retry for transient model-endpoint failures during ingest.

Observed in production: bulk ingest against a ~60 tok/s vLLM backend
saturates the endpoint and per-call requests time out transiently;
without retry, whole documents fail. Retry only the transient classes
(network timeouts, connection errors, HTTP 429/5xx) with bounded
exponential backoff — everything else (auth, bad request, parse errors)
surfaces immediately, and an exhausted budget surfaces the last error.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

import httpx

logger = logging.getLogger(__name__)

T = TypeVar("T")

# 单次退避上限，让重试预算的总等待时间保持有界
# （默认 3 次重试 ≈ 最多 ~90 s）。
MAX_BACKOFF_SECONDS = 30.0


def is_transient(error: BaseException) -> bool:
    """True for endpoint-saturation failures worth retrying."""
    if isinstance(error, httpx.TimeoutException | httpx.TransportError):
        return True
    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        return status == 429 or status >= 500
    return False


async def retry_transient(
    call: Callable[[], Awaitable[T]],
    *,
    retries: int = 3,
    backoff_base_seconds: float = 2.0,
    description: str = "",
) -> T:
    """调用 call()，瞬时失败按指数退避（带抖动）重试。

    retries=0 表示不重试；非瞬时异常立即抛出；预算耗尽抛出最后一次
    异常，由调用方决定如何收尾（pipeline 会标记文档 failed）。
    """
    attempt = 0
    while True:
        try:
            return await call()
        except Exception as error:
            if not is_transient(error) or attempt >= retries:
                raise
            attempt += 1
            delay = min(
                backoff_base_seconds * (2 ** (attempt - 1)), MAX_BACKOFF_SECONDS
            )
            delay *= 0.5 + random.random()  # 抖动，避免并发风暴同步重试
            logger.warning(
                "%s 第 %d/%d 次尝试瞬时失败（%s: %s），%.1fs 后重试",
                description or "模型调用",
                attempt,
                retries,
                type(error).__name__,
                error,
                delay,
            )
            await asyncio.sleep(delay)
