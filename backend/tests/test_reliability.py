import time

import pytest

from app.nl2sql import llm_client
from app.nl2sql.llm_client import _CircuitBreaker


def test_circuit_breaker_opens_and_resets():
    cb = _CircuitBreaker(threshold=3, reset_seconds=1)
    assert not cb.is_open()
    for _ in range(3):
        cb.record_failure()
    assert cb.is_open()          # tripped
    time.sleep(1.1)
    assert not cb.is_open()      # half-open after cool-down
    cb.record_success()
    assert not cb.is_open()


def test_call_local_llm_retries_then_raises_safe_error(monkeypatch):
    calls = {"n": 0}

    def boom(*a, **k):
        calls["n"] += 1
        raise llm_client.RequestsConnectionError("down")

    # Fresh breaker so prior tests don't interfere.
    monkeypatch.setattr(llm_client, "_breaker", _CircuitBreaker(threshold=99, reset_seconds=1))
    monkeypatch.setattr(llm_client.requests, "post", boom)
    monkeypatch.setattr(llm_client.time, "sleep", lambda *_: None)

    with pytest.raises(RuntimeError) as exc:
        llm_client.call_local_llm("hi")

    assert "temporarily unavailable" in str(exc.value).lower()
    assert calls["n"] >= 2  # retried


def test_open_breaker_fails_fast(monkeypatch):
    opened = _CircuitBreaker(threshold=1, reset_seconds=60)
    opened.record_failure()  # trips immediately (threshold=1)
    monkeypatch.setattr(llm_client, "_breaker", opened)

    def should_not_be_called(*a, **k):
        raise AssertionError("network called while breaker open")

    monkeypatch.setattr(llm_client.requests, "post", should_not_be_called)
    with pytest.raises(RuntimeError):
        llm_client.call_local_llm("hi")
