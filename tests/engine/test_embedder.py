import pytest

from src.engine.components import embedder as embedder_mod


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeClient:
    """Records requests; returns OpenAI /v1/embeddings-shaped responses.

    Responses are reversed so index-mapping (not order) must be used.
    """

    calls: list[dict] = []

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, headers=None):
        type(self).calls.append({"url": url, "json": json, "headers": headers})
        data = [
            {"index": i, "embedding": [float(i)] * 768}
            for i, _ in enumerate(json["input"])
        ]
        data.reverse()
        return _FakeResp({"data": data})


async def test_embed_batch_posts_openai_embeddings_shape(monkeypatch):
    _FakeClient.calls = []
    monkeypatch.setattr(embedder_mod.httpx, "AsyncClient", _FakeClient)
    e = embedder_mod.Embedder(
        base_url="http://embed.example/v1/", model="embed-model", api_key="secret"
    )
    out = await e.embed_batch(["a", "b", "c"])
    assert len(out) == 3
    assert all(len(v) == 768 for v in out)
    assert [v[0] for v in out] == [0.0, 1.0, 2.0]
    call = _FakeClient.calls[0]
    assert call["url"] == "http://embed.example/v1/embeddings"
    assert call["json"] == {"model": "embed-model", "input": ["a", "b", "c"]}
    assert call["headers"]["Authorization"] == "Bearer secret"


async def test_embed_batch_no_auth_header_without_key(monkeypatch):
    _FakeClient.calls = []
    monkeypatch.setattr(embedder_mod.httpx, "AsyncClient", _FakeClient)
    e = embedder_mod.Embedder(base_url="http://embed.example/v1", model="m", api_key="")
    await e.embed_batch(["a"])
    assert "Authorization" not in _FakeClient.calls[0]["headers"]


async def test_embed_text_uses_batch_endpoint(monkeypatch):
    _FakeClient.calls = []
    monkeypatch.setattr(embedder_mod.httpx, "AsyncClient", _FakeClient)
    e = embedder_mod.Embedder(base_url="http://embed.example/v1", model="m")
    vec = await e.embed_text("hello")
    assert len(vec) == 768
    assert _FakeClient.calls[0]["json"]["input"] == ["hello"]


async def test_embed_batch_empty():
    e = embedder_mod.Embedder()
    assert await e.embed_batch([]) == []


async def test_wrong_dimension_fails_fast(monkeypatch):
    class ShortClient(_FakeClient):
        async def post(self, url, json=None, headers=None):
            return _FakeResp({"data": [{"index": 0, "embedding": [0.1] * 4}]})

    monkeypatch.setattr(embedder_mod.httpx, "AsyncClient", ShortClient)
    e = embedder_mod.Embedder(base_url="http://embed.example/v1", model="m")
    with pytest.raises(ValueError, match="4-dim"):
        await e.embed_text("hello")


async def test_missing_index_fails_fast(monkeypatch):
    class MissingIndexClient(_FakeClient):
        async def post(self, url, json=None, headers=None):
            # two inputs, only one vector returned
            return _FakeResp({"data": [{"index": 1, "embedding": [0.1] * 768}]})

    monkeypatch.setattr(embedder_mod.httpx, "AsyncClient", MissingIndexClient)
    e = embedder_mod.Embedder(base_url="http://embed.example/v1", model="m")
    with pytest.raises(ValueError, match="missing"):
        await e.embed_batch(["a", "b"])
