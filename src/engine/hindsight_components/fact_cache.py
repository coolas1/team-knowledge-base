"""Bounded process-local working set; authoritative reads still enforce access."""

from collections import OrderedDict, Counter
from dataclasses import asdict, is_dataclass
import json
from copy import deepcopy
import re
import time

from .utils import estimate_tokens
from .types import RecallFilter


def cache_key(scope, filters=None):
    """Stable content scope; request deadlines are enforced separately."""

    def normalize(value):
        if is_dataclass(value):
            value = asdict(value)
        if isinstance(value, dict):
            return {k: normalize(v) for k, v in sorted(value.items())}
        if isinstance(value, (tuple, list)):
            return sorted(
                (normalize(v) for v in value),
                key=lambda v: json.dumps(v, sort_keys=True, default=str),
            )
        return value

    payload = asdict(filters or RecallFilter())
    for key in ("timeout_seconds", "max_tokens", "max_candidates", "include"):
        payload.pop(key)
    return json.dumps(
        [normalize(scope), normalize(payload)], sort_keys=True, default=str
    )


def same_fact(cached, current):
    return (
        cached.get("text") == current.get("text")
        and cached.get("updated_at", cached.get("mentioned_at"))
        == current.get("updated_at")
        and cached.get("metadata", {}).get("memory_version")
        == current.get("metadata", {}).get("memory_version")
    )


def terms(text: str) -> set[str]:
    words = set(re.findall(r"[a-z0-9_]+", text.casefold()))
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        words.update(run[i : i + 2] for i in range(max(1, len(run) - 1)))
    return words


class FactCache:
    """TTL expires since last use; capacity evicts least recently used facts."""

    def __init__(self, capacity=256, ttl=1800, *, clock=time.monotonic):
        if capacity < 0 or ttl <= 0:
            raise ValueError("invalid fact cache bounds")
        self.capacity = capacity
        self.ttl = ttl
        self.clock = clock
        self._entries = OrderedDict()
        self._counts = Counter()

    def _expire(self):
        now = self.clock()
        for key, (used, _) in list(self._entries.items()):
            if now - used >= self.ttl:
                del self._entries[key]
                self._counts["expired"] += 1

    def discard(self, scope, identity):
        if self._entries.pop((scope, identity), None) is not None:
            self._counts["invalidated"] += 1

    def remember(self, scope, facts):
        self._expire()
        if not self.capacity:
            return
        for fact in facts:
            if fact.get("type") not in {"world", "experience"} or fact.get(
                "metadata", {}
            ).get("is_source_chunk"):
                continue
            if estimate_tokens(str(fact.get("text", ""))) > 1200:
                continue
            key = (scope, str(fact["id"]))
            if key not in self._entries:
                self._counts["admitted"] += 1
            self._entries[key] = (self.clock(), deepcopy(fact))
            self._entries.move_to_end(key)
            while len(self._entries) > self.capacity:
                self._entries.popitem(last=False)
                self._counts["evicted"] += 1

    def candidates(self, scope, query, *, limit, max_tokens):
        self._expire()
        facts = [fact for (key, _), (_, fact) in self._entries.items() if key == scope]
        selected = self.select(query, facts, limit=limit, max_tokens=max_tokens)
        self._counts["lookups"] += 1
        if not selected:
            self._counts["misses"] += 1
        return selected

    def record_hits(self, count):
        self._counts["hits"] += count

    def stats(self):
        self._expire()
        return {**dict(self._counts), "entries": len(self._entries)}

    @staticmethod
    def select(query, facts, *, limit, max_tokens):
        if limit < 1 or max_tokens < 1:
            return []
        query_terms = terms(query)
        ranked = sorted(
            facts,
            key=lambda fact: len(query_terms & terms(str(fact.get("text", "")))),
            reverse=True,
        )
        result = []
        spent = 0
        for fact in ranked:
            if not query_terms.intersection(terms(str(fact.get("text", "")))):
                continue
            cost = estimate_tokens(str(fact.get("text", "")))
            if spent + cost > max_tokens:
                continue
            result.append(deepcopy(fact))
            spent += cost
            if len(result) >= limit:
                break
        return result
