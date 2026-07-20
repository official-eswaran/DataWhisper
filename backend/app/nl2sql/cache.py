"""LLM response cache.

At temperature 0.1 the model is near-deterministic, and the NL→SQL prompt fully
encodes the schema + history + question, so identical prompts can safely reuse a
prior response. This turns repeated questions (dashboards, common asks) from a
multi-second model call into a sub-millisecond lookup.

Backed by Redis when ``REDIS_URL`` is set (shared across replicas), else an
in-process TTL+LRU cache. Keyed by hash(model + prompt) so a model change
invalidates the cache.
"""
from __future__ import annotations

import hashlib
import threading
import time
from collections import OrderedDict

from app.core.config import settings

_PREFIX = "dw:llmcache:"


def _key(prompt: str) -> str:
    digest = hashlib.sha256(f"{settings.LLM_MODEL}\x1f{prompt}".encode()).hexdigest()
    return _PREFIX + digest


class InMemoryLLMCache:
    def __init__(self, ttl_seconds: int, max_entries: int):
        self._ttl = ttl_seconds
        self._max = max_entries
        self._lock = threading.RLock()
        self._data: OrderedDict[str, tuple[str, float]] = OrderedDict()

    def get(self, prompt: str) -> str | None:
        key = _key(prompt)
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            value, expiry = entry
            if time.monotonic() > expiry:
                del self._data[key]
                return None
            self._data.move_to_end(key)
            return value

    def set(self, prompt: str, value: str) -> None:
        key = _key(prompt)
        with self._lock:
            self._data[key] = (value, time.monotonic() + self._ttl)
            self._data.move_to_end(key)
            while len(self._data) > self._max:
                self._data.popitem(last=False)


class RedisLLMCache:
    def __init__(self, client, ttl_seconds: int):
        self._r = client
        self._ttl = ttl_seconds

    def get(self, prompt: str) -> str | None:
        val = self._r.get(_key(prompt))
        if val is None:
            return None
        return val.decode() if isinstance(val, bytes) else val

    def set(self, prompt: str, value: str) -> None:
        self._r.set(_key(prompt), value, ex=self._ttl)


def build_llm_cache():
    ttl = settings.LLM_CACHE_TTL_SECONDS
    if settings.REDIS_URL:
        import redis

        client = redis.from_url(settings.REDIS_URL, socket_connect_timeout=2, socket_timeout=2)
        return RedisLLMCache(client, ttl)
    return InMemoryLLMCache(ttl_seconds=ttl, max_entries=settings.LLM_CACHE_MAX_ENTRIES)


llm_cache = build_llm_cache()
