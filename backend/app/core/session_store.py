"""Conversation + schema store.

Two interchangeable backends behind one interface:

* :class:`InMemoryConversationStore` — thread-safe, TTL + LRU. Correct for a
  single worker only (state is per-process).
* :class:`RedisConversationStore` — shared across workers/replicas. Selected
  automatically when ``settings.REDIS_URL`` is set, which is what makes
  ``WEB_CONCURRENCY > 1`` and horizontal scaling correct.

All call sites use the module-level ``conversation_store`` and the same five
methods, so switching backends is purely a configuration change.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import OrderedDict
from typing import Any, Protocol

from app.core.config import settings

logger = logging.getLogger("datawhisper.session_store")

_MAX_TURNS = 6  # last 3 Q&A pairs


class ConversationBackend(Protocol):
    def get_history(self, session_id: str) -> list[dict]: ...
    def append_turn(self, session_id: str, user_msg: str, assistant_msg: str) -> None: ...
    def get_schema(self, session_id: str) -> str | None: ...
    def set_schema(self, session_id: str, schema: str) -> None: ...
    def invalidate(self, session_id: str) -> None: ...


# ── In-memory backend ─────────────────────────────────────────────────────────

class InMemoryConversationStore:
    def __init__(self, ttl_seconds: int, max_entries: int):
        self._ttl = ttl_seconds
        self._max = max_entries
        self._lock = threading.RLock()
        self._data: OrderedDict[str, dict[str, Any]] = OrderedDict()

    def _evict_locked(self) -> None:
        now = time.monotonic()
        for k in [k for k, v in self._data.items() if now - v["ts"] > self._ttl]:
            del self._data[k]
        while len(self._data) > self._max:
            self._data.popitem(last=False)

    def _touch_locked(self, session_id: str) -> dict[str, Any]:
        entry = self._data.get(session_id)
        if entry is None:
            entry = {"history": [], "schema": None, "ts": time.monotonic()}
            self._data[session_id] = entry
        else:
            entry["ts"] = time.monotonic()
            self._data.move_to_end(session_id)
        return entry

    def get_history(self, session_id: str) -> list[dict]:
        with self._lock:
            self._evict_locked()
            return list(self._touch_locked(session_id)["history"])

    def append_turn(self, session_id: str, user_msg: str, assistant_msg: str) -> None:
        with self._lock:
            entry = self._touch_locked(session_id)
            entry["history"].append({"role": "user", "content": user_msg})
            entry["history"].append({"role": "assistant", "content": assistant_msg})
            entry["history"] = entry["history"][-_MAX_TURNS:]
            self._evict_locked()

    def get_schema(self, session_id: str) -> str | None:
        with self._lock:
            self._evict_locked()
            return self._touch_locked(session_id)["schema"]

    def set_schema(self, session_id: str, schema: str) -> None:
        with self._lock:
            self._touch_locked(session_id)["schema"] = schema

    def invalidate(self, session_id: str) -> None:
        with self._lock:
            self._data.pop(session_id, None)


# ── Redis backend ─────────────────────────────────────────────────────────────

class RedisConversationStore:
    def __init__(self, redis_client, ttl_seconds: int):
        self._r = redis_client
        self._ttl = ttl_seconds

    @staticmethod
    def _hist_key(sid: str) -> str:
        return f"dw:hist:{sid}"

    @staticmethod
    def _schema_key(sid: str) -> str:
        return f"dw:schema:{sid}"

    def get_history(self, session_id: str) -> list[dict]:
        raw = self._r.lrange(self._hist_key(session_id), 0, -1)
        out = []
        for item in raw:
            try:
                out.append(json.loads(item))
            except (json.JSONDecodeError, TypeError):
                continue
        return out

    def append_turn(self, session_id: str, user_msg: str, assistant_msg: str) -> None:
        key = self._hist_key(session_id)
        pipe = self._r.pipeline()
        pipe.rpush(key, json.dumps({"role": "user", "content": user_msg}))
        pipe.rpush(key, json.dumps({"role": "assistant", "content": assistant_msg}))
        pipe.ltrim(key, -_MAX_TURNS, -1)
        pipe.expire(key, self._ttl)
        pipe.execute()

    def get_schema(self, session_id: str) -> str | None:
        val = self._r.get(self._schema_key(session_id))
        if val is None:
            return None
        return val.decode() if isinstance(val, bytes) else val

    def set_schema(self, session_id: str, schema: str) -> None:
        self._r.set(self._schema_key(session_id), schema, ex=self._ttl)

    def invalidate(self, session_id: str) -> None:
        self._r.delete(self._hist_key(session_id), self._schema_key(session_id))


# ── Factory ───────────────────────────────────────────────────────────────────

_NO_SHARED_STATE = (
    "REDIS_URL is unset in a non-DEBUG environment: the LLM cache, conversation "
    "store, and rate limiter are per-process. This is correct for a single "
    "replica ONLY — set REDIS_URL before running more than one, or state will "
    "silently diverge between pods (per-pod cache, split conversation history, "
    "per-pod rate limits)."
)


def check_shared_state() -> list[str]:
    """Warn — or refuse to start — when production has no shared state backend.

    The LLM cache, conversation store, and (via slowapi) rate limiting all fall
    back to in-process state when ``REDIS_URL`` is unset. Single-replica
    production is legitimate and a process can't see its own replica count, so
    the default is a loud warning rather than a failure. A deployment that can
    scale past one replica sets ``REQUIRE_SHARED_STATE=true`` and gets a hard
    failure instead — which is the point of issue #29: the bad case is silent,
    not impossible.

    Returns the warnings so tests can assert on them; also logged at WARNING.
    """
    warnings: list[str] = []
    if not settings.DEBUG and not settings.REDIS_URL:
        if settings.REQUIRE_SHARED_STATE:
            raise RuntimeError(
                f"{_NO_SHARED_STATE} REQUIRE_SHARED_STATE=true, so this process "
                "refuses to start without it."
            )
        warnings.append(_NO_SHARED_STATE)
    for warning in warnings:
        logger.warning("startup: %s", warning)
    return warnings


def build_conversation_store() -> ConversationBackend:
    ttl = settings.CONVERSATION_TTL_MINUTES * 60
    if settings.REDIS_URL:
        import redis  # imported lazily so the dep is optional in dev

        client = redis.from_url(settings.REDIS_URL, socket_connect_timeout=2, socket_timeout=2)
        return RedisConversationStore(client, ttl_seconds=ttl)
    return InMemoryConversationStore(ttl_seconds=ttl, max_entries=settings.MAX_ACTIVE_CONVERSATIONS)


conversation_store: ConversationBackend = build_conversation_store()
