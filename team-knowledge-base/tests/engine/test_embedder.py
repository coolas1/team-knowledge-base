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
    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None):
        inp = json["input"]
        if isinstance(inp, list):
            return _FakeResp({"embeddings": [[0.1] * 768 for _ in inp]})
        return _FakeResp({"embeddings": [[0.2] * 768]})


async def test_embed_text_uses_ollama(monkeypatch):
    monkeypatch.setattr(embedder_mod.httpx, "AsyncClient", _FakeClient)
    e = embedder_mod.Embedder()
    vec = await e.embed_text("hello")
    assert len(vec) == 768


async def test_embed_batch_empty():
    e = embedder_mod.Embedder()
    assert await e.embed_batch([]) == []


async def test_embed_batch_returns_one_vec_per_input(monkeypatch):
    monkeypatch.setattr(embedder_mod.httpx, "AsyncClient", _FakeClient)
    e = embedder_mod.Embedder()
    out = await e.embed_batch(["a", "b", "c"])
    assert len(out) == 3
    assert all(len(v) == 768 for v in out)
