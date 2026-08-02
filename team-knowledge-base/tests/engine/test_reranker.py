import sys
import types

from config.settings import settings
from src.engine.components import reranker as reranker_mod


class _FakeCrossEncoder:
    def __init__(self, model_name):
        self.model_name = model_name

    def predict(self, pairs):
        # higher score when the text contains the query term
        return [10.0 if q in t else -5.0 for q, t in pairs]


class _FakeResp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


class _FakeClient:
    """Mimics httpx.Client context manager + .post()."""

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, headers=None, json=None):
        docs = json["documents"]
        results = [
            {"index": i, "relevance_score": 1.0 if json["query"] in d else 0.0}
            for i, d in enumerate(docs)
        ]
        return _FakeResp({"results": results})


def _install_fake_sentence_transformers(monkeypatch):
    fake = types.ModuleType("sentence_transformers")
    fake.CrossEncoder = _FakeCrossEncoder
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake)


# ── LocalReranker (CrossEncoder via lazy import) ──────────────────────


def test_local_reranker_scores_pairs(monkeypatch):
    _install_fake_sentence_transformers(monkeypatch)
    r = reranker_mod.LocalReranker("any-model")
    assert r.rerank("alice", ["alice is here", "bob is gone"]) == [10.0, -5.0]


def test_local_reranker_empty(monkeypatch):
    _install_fake_sentence_transformers(monkeypatch)
    r = reranker_mod.LocalReranker("any-model")
    assert r.rerank("q", []) == []


# ── HttpReranker (external /v1/rerank API) ────────────────────────────


def test_http_reranker_maps_scores_by_index(monkeypatch):
    monkeypatch.setattr(reranker_mod.httpx, "Client", _FakeClient)
    r = reranker_mod.HttpReranker("https://example.com/v1/", "rerank-model", "key")
    assert r.rerank("alice", ["alice is here", "bob is gone"]) == [1.0, 0.0]


def test_http_reranker_empty(monkeypatch):
    monkeypatch.setattr(reranker_mod.httpx, "Client", _FakeClient)
    r = reranker_mod.HttpReranker("https://example.com/v1", "m", "k")
    assert r.rerank("q", []) == []


# ── NoopReranker ──────────────────────────────────────────────────────


def test_noop_reranker_uniform():
    r = reranker_mod.NoopReranker()
    assert r.rerank("q", ["a", "b", "c"]) == [1.0, 1.0, 1.0]
    assert r.rerank("q", []) == []


# ── get_reranker() factory ────────────────────────────────────────────


def _reset():
    reranker_mod._reranker_instance = None


def test_get_reranker_none(monkeypatch):
    monkeypatch.setattr(settings, "reranker_provider", "none")
    _reset()
    assert isinstance(reranker_mod.get_reranker(), reranker_mod.NoopReranker)
    _reset()


def test_get_reranker_http(monkeypatch):
    monkeypatch.setattr(settings, "reranker_provider", "http")
    monkeypatch.setattr(settings, "reranker_base_url", "https://example.com/v1")
    monkeypatch.setattr(settings, "reranker_model", "m")
    monkeypatch.setattr(settings, "reranker_api_key", "k")
    _reset()
    r = reranker_mod.get_reranker()
    assert isinstance(r, reranker_mod.HttpReranker)
    assert r._base_url == "https://example.com/v1"
    assert r._model == "m"
    assert r._api_key == "k"
    _reset()


def test_get_reranker_http_without_base_url_falls_back_to_noop(monkeypatch):
    monkeypatch.setattr(settings, "reranker_provider", "http")
    monkeypatch.setattr(settings, "reranker_base_url", "")
    _reset()
    assert isinstance(reranker_mod.get_reranker(), reranker_mod.NoopReranker)
    _reset()


def test_get_reranker_local(monkeypatch):
    _install_fake_sentence_transformers(monkeypatch)
    monkeypatch.setattr(settings, "reranker_provider", "local")
    monkeypatch.setattr(settings, "reranker_model", "any-model")
    _reset()
    assert isinstance(reranker_mod.get_reranker(), reranker_mod.LocalReranker)
    _reset()


def test_get_reranker_singleton(monkeypatch):
    monkeypatch.setattr(settings, "reranker_provider", "none")
    _reset()
    a = reranker_mod.get_reranker()
    b = reranker_mod.get_reranker()
    assert a is b
    _reset()
