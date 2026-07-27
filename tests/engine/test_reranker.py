from src.engine.components import reranker as reranker_mod


class _FakeCrossEncoder:
    def __init__(self, model_name):
        self.model_name = model_name

    def predict(self, pairs):
        # higher score when text contains the query term
        return [10.0 if q in t else -5.0 for q, t in pairs]


def test_rerank_scores_pairs(monkeypatch):
    monkeypatch.setattr(reranker_mod, "CrossEncoder", _FakeCrossEncoder)
    r = reranker_mod.Reranker()
    scores = r.rerank("alice", ["alice is here", "bob is gone"])
    assert scores == [10.0, -5.0]


def test_rerank_empty(monkeypatch):
    monkeypatch.setattr(reranker_mod, "CrossEncoder", _FakeCrossEncoder)
    r = reranker_mod.Reranker()
    assert r.rerank("q", []) == []


def test_get_reranker_singleton(monkeypatch):
    monkeypatch.setattr(reranker_mod, "CrossEncoder", _FakeCrossEncoder)
    reranker_mod._reranker_instance = None
    a = reranker_mod.get_reranker()
    b = reranker_mod.get_reranker()
    assert a is b
    reranker_mod._reranker_instance = None
