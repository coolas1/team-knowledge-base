"""瞬时失败重试（engine.ingest.llm_retries / llm_backoff_base_seconds）。"""

from __future__ import annotations

import httpx
import pytest

from src.engine.components.retry import is_transient, retry_transient


def _status_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://model/v1/chat/completions")
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError(f"HTTP {status}", request=request, response=response)


class _FlakyCall:
    """fake 端点：按脚本依次抛异常，耗尽后返回结果。"""

    def __init__(self, script: list[BaseException], result="ok"):
        self.script = list(script)
        self.result = result
        self.calls = 0

    async def __call__(self):
        self.calls += 1
        if self.script:
            raise self.script.pop(0)
        return self.result


async def test_transient_failures_recover_with_backoff():
    flaky = _FlakyCall(
        [httpx.ConnectError("saturation"), httpx.ReadTimeout("slow")]
    )

    result = await retry_transient(
        flaky, retries=3, backoff_base_seconds=0.001, description="分析"
    )

    assert result == "ok"
    assert flaky.calls == 3  # 2 次失败 + 1 次成功


async def test_non_transient_exception_is_not_retried():
    flaky = _FlakyCall([ValueError("bad request")])

    with pytest.raises(ValueError, match="bad request"):
        await retry_transient(flaky, retries=3, backoff_base_seconds=0.001)

    assert flaky.calls == 1


async def test_exhausted_budget_surfaces_last_error():
    flaky = _FlakyCall([httpx.ConnectTimeout("down")] * 10)

    with pytest.raises(httpx.ConnectTimeout):
        await retry_transient(flaky, retries=2, backoff_base_seconds=0.001)

    assert flaky.calls == 3  # 1 次原始调用 + 2 次重试


def test_is_transient_classification():
    assert is_transient(httpx.ConnectError("refused"))
    assert is_transient(httpx.ReadTimeout("timeout"))
    assert is_transient(_status_error(429))
    assert is_transient(_status_error(500))
    assert is_transient(_status_error(503))
    assert not is_transient(_status_error(400))
    assert not is_transient(_status_error(401))
    assert not is_transient(_status_error(404))
    assert not is_transient(ValueError("nope"))


async def test_zero_retries_disables_retry():
    flaky = _FlakyCall([httpx.ConnectError("down")] * 5)

    with pytest.raises(httpx.ConnectError):
        await retry_transient(flaky, retries=0, backoff_base_seconds=0.001)

    assert flaky.calls == 1
