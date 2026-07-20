"""Tests for the LLM response cache (issue #2 / M11).

Covers the in-memory TTL+LRU cache, the Redis-backed cache (via fakeredis),
the model-aware cache key, and — the acceptance criterion — that an identical
prompt is served from cache without a second model call.
"""
import pytest

from app.core.config import settings
from app.nl2sql import cache as cache_mod
from app.nl2sql import llm_client
from app.nl2sql.cache import InMemoryLLMCache, RedisLLMCache, build_llm_cache
from app.nl2sql.llm_client import _CircuitBreaker


class FakeClock:
    """Deterministic replacement for time.monotonic()."""

    def __init__(self, start: float = 1000.0):
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


@pytest.fixture
def clock(monkeypatch):
    c = FakeClock()
    monkeypatch.setattr(cache_mod.time, "monotonic", c)
    return c


# ── In-memory cache ────────────────────────────────────────────────────────

def test_inmemory_set_get_roundtrip(clock):
    c = InMemoryLLMCache(ttl_seconds=60, max_entries=10)
    assert c.get("select?") is None
    c.set("select?", "SELECT 1")
    assert c.get("select?") == "SELECT 1"


def test_inmemory_ttl_expiry(clock):
    c = InMemoryLLMCache(ttl_seconds=60, max_entries=10)
    c.set("q", "v")
    clock.advance(59)
    assert c.get("q") == "v"          # still fresh
    clock.advance(2)                  # now past the 60s TTL
    assert c.get("q") is None         # expired and evicted


def test_inmemory_lru_eviction(clock):
    c = InMemoryLLMCache(ttl_seconds=60, max_entries=2)
    c.set("a", "1")
    c.set("b", "2")
    c.set("c", "3")                   # exceeds cap -> oldest ("a") dropped
    assert c.get("a") is None
    assert c.get("b") == "2"
    assert c.get("c") == "3"


def test_inmemory_get_refreshes_recency(clock):
    c = InMemoryLLMCache(ttl_seconds=60, max_entries=2)
    c.set("a", "1")
    c.set("b", "2")
    assert c.get("a") == "1"          # touch "a" so it's most-recent
    c.set("c", "3")                   # now "b" is the LRU victim, not "a"
    assert c.get("a") == "1"
    assert c.get("b") is None
    assert c.get("c") == "3"


def test_key_is_model_aware(clock, monkeypatch):
    c = InMemoryLLMCache(ttl_seconds=60, max_entries=10)
    monkeypatch.setattr(settings, "LLM_MODEL", "model-a")
    c.set("same prompt", "answer-a")
    assert c.get("same prompt") == "answer-a"
    monkeypatch.setattr(settings, "LLM_MODEL", "model-b")
    # A different model must not read the previous model's cached answer.
    assert c.get("same prompt") is None


# ── Redis-backed cache (fakeredis) ─────────────────────────────────────────

def test_redis_cache_roundtrip_and_ttl():
    fakeredis = pytest.importorskip("fakeredis")
    client = fakeredis.FakeStrictRedis()
    c = RedisLLMCache(client, ttl_seconds=123)
    assert c.get("q") is None
    c.set("q", "SELECT 1")
    assert c.get("q") == "SELECT 1"   # bytes from redis get decoded
    ttl = client.ttl(cache_mod._key("q"))
    assert 0 < ttl <= 123             # expiry was applied


def test_build_llm_cache_selects_backend(monkeypatch):
    monkeypatch.setattr(settings, "REDIS_URL", "")
    assert isinstance(build_llm_cache(), InMemoryLLMCache)

    fakeredis = pytest.importorskip("fakeredis")
    import redis

    monkeypatch.setattr(settings, "REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setattr(redis, "from_url", lambda *a, **k: fakeredis.FakeStrictRedis())
    assert isinstance(build_llm_cache(), RedisLLMCache)


# ── call_local_llm cache-hit behaviour (acceptance) ────────────────────────

class _FakeResp:
    def __init__(self, text: str):
        self._text = text

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return {"response": self._text}


@pytest.fixture
def fresh_cache(monkeypatch):
    """Isolate call_local_llm from cache state left by other tests."""
    fresh = InMemoryLLMCache(ttl_seconds=3600, max_entries=100)
    monkeypatch.setattr(llm_client, "llm_cache", fresh)
    # A permissive breaker so unrelated prior failures don't fail us fast.
    monkeypatch.setattr(llm_client, "_breaker", _CircuitBreaker(threshold=99, reset_seconds=1))
    return fresh


def test_call_local_llm_cache_hit_skips_second_model_call(monkeypatch, fresh_cache):
    calls = {"n": 0}

    def fake_post(*a, **k):
        calls["n"] += 1
        return _FakeResp(f"SELECT {calls['n']}")

    monkeypatch.setattr(llm_client.requests, "post", fake_post)

    first = llm_client.call_local_llm("count the rows")
    second = llm_client.call_local_llm("count the rows")

    assert calls["n"] == 1            # model hit exactly once
    assert first == "SELECT 1"
    assert second == first            # identical prompt served from cache


def test_call_local_llm_records_hit_and_miss_metrics(monkeypatch, fresh_cache):
    from app.core.observability import LLM_CACHE_HITS, LLM_CACHE_MISSES

    monkeypatch.setattr(llm_client.requests, "post", lambda *a, **k: _FakeResp("SELECT 1"))

    hits0 = LLM_CACHE_HITS._value.get()
    miss0 = LLM_CACHE_MISSES._value.get()

    llm_client.call_local_llm("unique-prompt-metrics")   # miss -> model call
    llm_client.call_local_llm("unique-prompt-metrics")   # hit -> cache

    assert LLM_CACHE_MISSES._value.get() - miss0 == 1
    assert LLM_CACHE_HITS._value.get() - hits0 == 1


def test_streaming_cache_lookup_records_hit_miss(monkeypatch, fresh_cache):
    from app.core.observability import LLM_CACHE_HITS, LLM_CACHE_MISSES

    hits0 = LLM_CACHE_HITS._value.get()
    miss0 = LLM_CACHE_MISSES._value.get()

    assert llm_client.llm_cache_lookup("stream-prompt") is None   # miss
    llm_client.llm_cache_store("stream-prompt", "SELECT 1")
    assert llm_client.llm_cache_lookup("stream-prompt") == "SELECT 1"  # hit

    assert LLM_CACHE_MISSES._value.get() - miss0 == 1
    assert LLM_CACHE_HITS._value.get() - hits0 == 1
