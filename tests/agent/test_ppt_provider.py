import base64
import io
import json
from unittest.mock import AsyncMock

import httpx
from PIL import Image
import pytest

from config.settings import ImageSettings
from src.agent.ppt.provider import SeedreamProvider, ImageProviderError, download_image


def config(**overrides):
    return ImageSettings(
        _env_file=None,
        **{
            "base_url": "https://ark.cn-beijing.volces.com/api/plan/v3",
            "model": "doubao-seedream-5.0-lite",
            "api_key": "test-only",
            **overrides,
        },
    )


def png():
    out = io.BytesIO()
    Image.new("RGB", (2560, 1440), "red").save(out, format="PNG")
    return out.getvalue()


@pytest.mark.parametrize(
    "fields",
    [
        {"api_key": ""},
        {"model": ""},
        {"base_url": ""},
        {"provider": "openai"},
        {"base_url": "https://ark.cn-beijing.volces.com/api/v3"},
        {"model": "gpt-image-2"},
    ],
)
def test_incomplete_or_wrong_route_rejected(fields):
    with pytest.raises(ValueError):
        SeedreamProvider(config(**fields))


async def test_seedream_reference_is_real_image_and_no_gpt_parameters():
    data = png()

    def handle(request):
        payload = json.loads(request.content)
        assert (
            payload["image"]
            == "data:image/png;base64," + base64.b64encode(data).decode()
        )
        assert payload["size"] == "2560x1440"
        assert not {"quality", "n", "input_fidelity"} & payload.keys()
        return httpx.Response(
            200,
            json={
                "model": "actual-version",
                "data": [{"b64_json": base64.b64encode(data).decode()}],
            },
            headers={"x-request-id": "test-id"},
        )

    result = await SeedreamProvider(
        config(), transport=httpx.MockTransport(handle)
    ).generate("slide", references=(data,))
    assert result.data == data and result.actual_model == "actual-version"
    assert result.usage is None and result.request_id == "test-id"


@pytest.mark.parametrize("status,unknown", [(429, False), (401, False), (500, True)])
async def test_errors_are_not_retried(status, unknown):
    count = 0

    def handle(request):
        nonlocal count
        count += 1
        return httpx.Response(status)

    with pytest.raises(ImageProviderError) as caught:
        await SeedreamProvider(
            config(), transport=httpx.MockTransport(handle)
        ).generate("slide")
    assert count == 1 and caught.value.unknown is unknown


async def test_timeout_and_missing_result_are_unknown():
    def timeout(request):
        raise httpx.ReadTimeout("timeout")

    for handler in [timeout, lambda request: httpx.Response(200, json={"data": []})]:
        with pytest.raises(ImageProviderError) as caught:
            await SeedreamProvider(
                config(), transport=httpx.MockTransport(handler)
            ).generate("slide")
        assert caught.value.unknown


async def test_url_download_pins_public_ip_and_rejects_redirect(monkeypatch):
    import asyncio

    monkeypatch.setattr(
        asyncio.get_running_loop(),
        "getaddrinfo",
        AsyncMock(return_value=[(2, 1, 6, "", ("8.8.8.8", 443))]),
    )

    def handle(request):
        assert request.url.host == "8.8.8.8"
        assert request.headers["host"] == "generated.byteimg.com"
        assert request.extensions["sni_hostname"] == "generated.byteimg.com"
        assert "authorization" not in request.headers
        return httpx.Response(302, headers={"location": "http://127.0.0.1"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(ImageProviderError, match="download_rejected"):
            await download_image(client, "https://generated.byteimg.com/a")


async def test_private_url_and_oversize_response_rejected(monkeypatch):
    from src.agent.ppt import provider

    monkeypatch.setattr(provider, "MAX_RESPONSE_BYTES", 10)
    async with httpx.AsyncClient() as client:
        with pytest.raises(ImageProviderError, match="untrusted"):
            await download_image(client, "http://127.0.0.1/a")
    with pytest.raises(ImageProviderError, match="size_limit"):
        await SeedreamProvider(
            config(),
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, content=b"x" * 100)
            ),
        ).generate("slide")
