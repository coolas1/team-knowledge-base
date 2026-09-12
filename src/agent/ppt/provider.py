"""Bounded Seedream requests with no implicit retries or billing-route fallback."""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
import hashlib
import io
import ipaddress
import json
import socket

import httpx
from PIL import Image

from config.settings import ImageSettings

MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_RESPONSE_BYTES = 30 * 1024 * 1024
SLIDE_SIZE = (2560, 1440)


class ImageProviderError(RuntimeError):
    def __init__(self, code: str, *, unknown=False, request_id=None):
        super().__init__(code)
        self.code = code
        self.unknown = unknown
        self.request_id = request_id


@dataclass(frozen=True)
class GeneratedImage:
    data: bytes
    mime: str
    sha256: str
    requested_model: str
    actual_model: str | None
    request_id: str | None
    usage: dict | None


def validate_image(data: bytes, *, slide=False) -> str:
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ImageProviderError("image_size_limit")
    try:
        with Image.open(io.BytesIO(data)) as im:
            if im.format not in {"PNG", "JPEG"} or im.width * im.height > 20_000_000:
                raise ImageProviderError("unsupported_image")
            if slide and im.size != SLIDE_SIZE:
                raise ImageProviderError("unexpected_slide_dimensions")
            mime = "image/png" if im.format == "PNG" else "image/jpeg"
            im.verify()
            return mime
    except ImageProviderError:
        raise
    except Exception as exc:
        raise ImageProviderError("invalid_image") from exc


async def _bounded_body(response: httpx.Response, limit: int) -> bytes:
    data = bytearray()
    async for part in response.aiter_bytes():
        data.extend(part)
        if len(data) > limit:
            raise ImageProviderError("response_size_limit", unknown=True)
    return bytes(data)


async def download_image(client: httpx.AsyncClient, url: str) -> bytes:
    target = httpx.URL(url)
    host = target.host
    if (
        target.scheme != "https"
        or target.port not in {None, 443}
        or target.username
        or target.password
        or not any(host.endswith(s) for s in (".volces.com", ".byteimg.com"))
    ):
        raise ImageProviderError("untrusted_image_url", unknown=True)
    # Pin a validated address, retaining TLS SNI. No second DNS resolution and
    # no redirects/proxy: validation cannot be bypassed by DNS rebinding.
    addresses = await asyncio.get_running_loop().getaddrinfo(
        host, 443, type=socket.SOCK_STREAM
    )
    ips = sorted({item[4][0] for item in addresses})
    if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
        raise ImageProviderError("private_image_address", unknown=True)
    async with client.stream(
        "GET",
        target.copy_with(host=ips[0]),
        headers={"Host": host},
        extensions={"sni_hostname": host},
        follow_redirects=False,
    ) as response:
        if response.status_code != 200:
            raise ImageProviderError("image_download_rejected", unknown=True)
        if response.headers.get("content-type", "").split(";")[0] not in {
            "image/png",
            "image/jpeg",
        }:
            raise ImageProviderError("image_content_type", unknown=True)
        return await _bounded_body(response, MAX_IMAGE_BYTES)


class SeedreamProvider:
    def __init__(self, config: ImageSettings, *, transport=None):
        config.require_ready()
        self.config = config
        self.transport = transport

    async def generate(
        self, prompt: str, *, references: tuple[bytes, ...] = ()
    ) -> GeneratedImage:
        self.config.require_ready()
        if not prompt.strip() or len(prompt) > 12000 or len(references) > 4:
            raise ValueError("Invalid image prompt or reference count")
        images = [
            "data:"
            + validate_image(data)
            + ";base64,"
            + base64.b64encode(data).decode()
            for data in references
        ]
        payload = {
            "model": self.config.model,
            "prompt": prompt,
            "size": "2560x1440",
            "response_format": "b64_json",
            "sequential_image_generation": "disabled",
            "watermark": False,
        }
        if images:
            payload["image"] = images if len(images) > 1 else images[0]
        request_id = None
        try:
            async with httpx.AsyncClient(
                timeout=180, trust_env=False, transport=self.transport
            ) as client:
                async with client.stream(
                    "POST",
                    self.config.base_url.rstrip("/") + "/images/generations",
                    headers={"Authorization": "Bearer " + self.config.api_key},
                    json=payload,
                ) as response:
                    request_id = response.headers.get(
                        "x-request-id"
                    ) or response.headers.get("x-tt-logid")
                    if response.status_code != 200:
                        raise ImageProviderError(
                            f"image_http_{response.status_code}",
                            unknown=response.status_code >= 500,
                            request_id=request_id,
                        )
                    result = json.loads(
                        await _bounded_body(response, MAX_RESPONSE_BYTES)
                    )
                entries = result.get("data")
                if not isinstance(entries, list) or len(entries) != 1:
                    raise ImageProviderError("missing_single_image", unknown=True)
                entry = entries[0]
                if entry.get("b64_json"):
                    data = base64.b64decode(entry["b64_json"], validate=True)
                elif entry.get("url"):
                    data = await download_image(client, entry["url"])
                else:
                    raise ImageProviderError("missing_image", unknown=True)
                mime = validate_image(data, slide=True)
                return GeneratedImage(
                    data,
                    mime,
                    hashlib.sha256(data).hexdigest(),
                    self.config.model,
                    result.get("model"),
                    request_id,
                    result.get("usage")
                    if isinstance(result.get("usage"), dict)
                    else None,
                )
        except ImageProviderError as exc:
            if exc.request_id is None:
                exc.request_id = request_id
            raise
        except Exception as exc:
            # A timeout or invalid successful response may already have incurred
            # a charge. The worker must never automatically repeat this call.
            raise ImageProviderError(
                "image_outcome_unknown", unknown=True, request_id=request_id
            ) from exc
