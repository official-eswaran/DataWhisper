"""Ollama client.

Production concerns handled here:

* Concurrency: a bounded semaphore caps simultaneous generations so a burst of
  users queues instead of overwhelming the single model server.
* Retry with exponential backoff on transient failures.
* Circuit breaker: after N consecutive failures the breaker opens and calls fail
  fast for a cool-down window, so a dead/slow Ollama does not tie up workers.
* Typed, user-safe errors; internals are logged, never returned to the client.
"""
from __future__ import annotations

import json as _json
import logging
import threading
import time

import requests
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import Timeout

from app.core.config import settings
from app.core.observability import LLM_CACHE_HITS, LLM_CACHE_MISSES, LLM_CALLS, LLM_FAILURES
from app.nl2sql.cache import llm_cache

logger = logging.getLogger("datawhisper.llm")

_llm_semaphore = threading.BoundedSemaphore(settings.LLM_MAX_CONCURRENCY)

_GENERATE_URL = f"{settings.OLLAMA_BASE_URL}/api/generate"
_OPTIONS = {"temperature": 0.1, "num_predict": 512}

_TRANSIENT = (RequestsConnectionError, Timeout)


class _CircuitBreaker:
    """Trips open after `threshold` consecutive failures; half-opens after
    `reset_seconds` to probe recovery."""

    def __init__(self, threshold: int, reset_seconds: int):
        self._threshold = threshold
        self._reset = reset_seconds
        self._failures = 0
        self._opened_at = 0.0
        self._lock = threading.Lock()

    def is_open(self) -> bool:
        with self._lock:
            if self._failures < self._threshold:
                return False
            # Open — allow a single probe once the cool-down elapses.
            if time.monotonic() - self._opened_at >= self._reset:
                return False
            return True

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = 0.0

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._failures >= self._threshold and self._opened_at == 0.0:
                self._opened_at = time.monotonic()


_breaker = _CircuitBreaker(
    settings.LLM_CIRCUIT_FAIL_THRESHOLD,
    settings.LLM_CIRCUIT_RESET_SECONDS,
)

_UNAVAILABLE = "The AI engine is temporarily unavailable. Please try again shortly."


def ollama_healthy() -> bool:
    try:
        return requests.get(f"{settings.OLLAMA_BASE_URL}/api/tags", timeout=3).status_code == 200
    except Exception:
        return False


def _base_payload(prompt: str, stream: bool) -> dict:
    return {"model": settings.LLM_MODEL, "prompt": prompt, "stream": stream, "options": _OPTIONS}


def _guard_breaker() -> None:
    if _breaker.is_open():
        raise RuntimeError(_UNAVAILABLE)


def _cache_get(prompt: str) -> str | None:
    if not settings.LLM_CACHE_ENABLED:
        return None
    try:
        return llm_cache.get(prompt)
    except Exception:
        return None  # cache must never break a request


def _cache_set(prompt: str, value: str) -> None:
    if not settings.LLM_CACHE_ENABLED or not value:
        return
    try:
        llm_cache.set(prompt, value)
    except Exception:
        pass


def llm_cache_lookup(prompt: str) -> str | None:
    """Public cache read for the streaming path; records the hit/miss metric."""
    value = _cache_get(prompt)
    (LLM_CACHE_HITS if value is not None else LLM_CACHE_MISSES).inc()
    return value


def llm_cache_store(prompt: str, value: str) -> None:
    _cache_set(prompt, value)


def call_local_llm(prompt: str) -> str:
    """Non-streaming generation with cache + retry + circuit breaker."""
    cached = _cache_get(prompt)
    if cached is not None:
        LLM_CACHE_HITS.inc()
        return cached
    LLM_CACHE_MISSES.inc()

    _guard_breaker()
    LLM_CALLS.inc()
    last_exc: Exception | None = None

    with _llm_semaphore:
        for attempt in range(1, settings.LLM_RETRY_ATTEMPTS + 1):
            try:
                response = requests.post(
                    _GENERATE_URL,
                    json=_base_payload(prompt, stream=False),
                    timeout=settings.LLM_TIMEOUT_SECONDS,
                )
                response.raise_for_status()
                _breaker.record_success()
                result = response.json()["response"]
                _cache_set(prompt, result)
                return result
            except _TRANSIENT as exc:
                last_exc = exc
                if attempt < settings.LLM_RETRY_ATTEMPTS:
                    time.sleep(settings.LLM_RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1)))
                    continue
            except Exception as exc:
                last_exc = exc
                break

    _breaker.record_failure()
    LLM_FAILURES.inc()
    logger.warning("LLM call failed after retries: %s", last_exc)
    raise RuntimeError(_UNAVAILABLE)


def stream_local_llm(prompt: str):
    """Stream tokens. Yields str tokens, then a final ("__done__", full_text)."""
    _guard_breaker()
    LLM_CALLS.inc()
    acquired = _llm_semaphore.acquire()
    try:
        try:
            response = requests.post(
                _GENERATE_URL,
                json=_base_payload(prompt, stream=True),
                stream=True,
                timeout=settings.LLM_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
        except Exception as exc:
            _breaker.record_failure()
            LLM_FAILURES.inc()
            logger.warning("LLM stream failed: %s", exc)
            raise RuntimeError(_UNAVAILABLE)

        full_text = ""
        for raw_line in response.iter_lines():
            if not raw_line:
                continue
            try:
                chunk = _json.loads(raw_line)
            except Exception:
                continue
            token = chunk.get("response", "")
            if token:
                full_text += token
                yield token
            if chunk.get("done"):
                break
        _breaker.record_success()
        yield ("__done__", full_text)
    finally:
        if acquired:
            _llm_semaphore.release()
