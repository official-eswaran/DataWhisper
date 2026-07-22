"""Production without Redis warns loudly instead of degrading silently (#29).

In-process cache/conversation/rate-limit state is correct for one replica and
silently wrong for several. The process can't know the replica count, so the
default is a warning; a deployment that *can* scale sets REQUIRE_SHARED_STATE
and gets a refusal to start. These tests pin both halves.
"""
import pytest

from app.core.config import settings
from app.core.session_store import check_shared_state


@pytest.fixture
def prod_without_redis(monkeypatch):
    monkeypatch.setattr(settings, "DEBUG", False)
    monkeypatch.setattr(settings, "REDIS_URL", "")
    monkeypatch.setattr(settings, "REQUIRE_SHARED_STATE", False)


def test_warns_in_prod_without_redis(prod_without_redis):
    warnings = check_shared_state()
    assert warnings and "REDIS_URL" in warnings[0]


def test_silent_when_redis_is_set(prod_without_redis, monkeypatch):
    monkeypatch.setattr(settings, "REDIS_URL", "redis://localhost:6379/0")
    assert check_shared_state() == []


def test_silent_in_debug(prod_without_redis, monkeypatch):
    """Dev/test run single-process on purpose — no warning."""
    monkeypatch.setattr(settings, "DEBUG", True)
    assert check_shared_state() == []


def test_warning_is_logged(prod_without_redis, caplog):
    with caplog.at_level("WARNING"):
        check_shared_state()
    assert any("REDIS_URL" in r.message for r in caplog.records)


def test_strict_mode_refuses_to_start(prod_without_redis, monkeypatch):
    """The multi-replica deployment opts into a hard failure."""
    monkeypatch.setattr(settings, "REQUIRE_SHARED_STATE", True)
    with pytest.raises(RuntimeError, match="REQUIRE_SHARED_STATE"):
        check_shared_state()


def test_strict_mode_is_satisfied_by_redis(prod_without_redis, monkeypatch):
    monkeypatch.setattr(settings, "REQUIRE_SHARED_STATE", True)
    monkeypatch.setattr(settings, "REDIS_URL", "redis://redis:6379/0")
    assert check_shared_state() == []


def test_strict_mode_does_not_break_debug(prod_without_redis, monkeypatch):
    """Strict + DEBUG (a developer copying prod env) must still boot."""
    monkeypatch.setattr(settings, "REQUIRE_SHARED_STATE", True)
    monkeypatch.setattr(settings, "DEBUG", True)
    assert check_shared_state() == []
