"""Reranker: scores (query, text) pairs for the search gatekeeper (second stage
of retrieval: vector recall -> rerank -> top-N).

Provider is selected by RERANKER_PROVIDER in .env:
  - "local" : CrossEncoder (BAAI/bge-reranker-v2-m3) via sentence-transformers.
              Needs the optional `reranker` extra (torch). Loads the model from
              the HuggingFace cache in offline mode.
  - "http"  : external /v1/rerank API (Cohere/Jina/OpenAI-compatible). No torch.
              Set RERANKER_BASE_URL, RERANKER_MODEL, RERANKER_API_KEY.
  - "none"  : disabled; returns uniform scores (vector recall order is kept).
"""
from __future__ import annotations

import logging
import os
from typing import Protocol

import httpx

from config.settings import settings

logger = logging.getLogger(__name__)


class RerankerProtocol(Protocol):
    def rerank(self, query: str, texts: list[str]) -> list[float]: ...


class NoopReranker:
    """Disabled reranker: uniform scores (caller keeps vector recall order)."""

    def rerank(self, query: str, texts: list[str]) -> list[float]:
        return [1.0] * len(texts)


class HttpReranker:
    """External /v1/rerank API reranker (Cohere/Jina/OpenAI-compatible).

    POSTs {base_url}/rerank with {model, query, documents, top_n} and expects
    {"results": [{"index": int, "relevance_score": float}, ...]}. Scores are
    mapped back to the input order by ``index``.
    """

    def __init__(self, base_url: str, model: str, api_key: str = "") -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key

    def rerank(self, query: str, texts: list[str]) -> list[float]:
        if not texts:
            return []
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        with httpx.Client(timeout=120.0) as client:
            resp = client.post(
                f"{self._base_url}/rerank",
                headers=headers,
                json={
                    "model": self._model,
                    "query": query,
                    "documents": texts,
                    "top_n": len(texts),
                },
            )
            resp.raise_for_status()
            data = resp.json()
        scores = [0.0] * len(texts)
        for r in data.get("results", []):
            idx = r.get("index")
            if isinstance(idx, int) and 0 <= idx < len(texts):
                scores[idx] = float(r.get("relevance_score", 0.0))
        return scores


class LocalReranker:
    """CrossEncoder reranker via sentence-transformers (needs the `reranker` extra).

    sentence_transformers is imported lazily so the default install (without the
    extra) does not require torch.
    """

    def __init__(self, model_name: str) -> None:
        # Prefer the local HF cache; never hit the network for the model.
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        from sentence_transformers import CrossEncoder  # heavy; lazy import

        logger.info("loading reranker model: %s ...", model_name)
        self.model = CrossEncoder(model_name)
        logger.info("reranker model loaded")

    def rerank(self, query: str, texts: list[str]) -> list[float]:
        if not texts:
            return []
        pairs = [(query, t) for t in texts]
        scores = self.model.predict(pairs)
        return [float(s) for s in scores]


_reranker_instance: RerankerProtocol | None = None


def get_reranker() -> RerankerProtocol:
    """Return the reranker singleton (lazy, built on first use)."""
    global _reranker_instance
    if _reranker_instance is None:
        _reranker_instance = _build_reranker()
    return _reranker_instance


def _build_reranker() -> RerankerProtocol:
    provider = settings.reranker_provider
    if provider == "local":
        return LocalReranker(settings.reranker_model or "BAAI/bge-reranker-v2-m3")
    if provider == "http":
        if not settings.reranker_base_url:
            logger.warning("RERANKER_PROVIDER=http but RERANKER_BASE_URL is empty; using noop")
            return NoopReranker()
        return HttpReranker(
            base_url=settings.reranker_base_url,
            model=settings.reranker_model,
            api_key=settings.reranker_api_key,
        )
    logger.info("reranker disabled (provider=%r); using noop", provider)
    return NoopReranker()
