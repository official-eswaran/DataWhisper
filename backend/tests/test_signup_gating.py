"""Signup gating + registration rate limiting (issue #21).

Open self-service signup is an abuse vector on a compute-heavy product: every
new org gets a free LLM budget, and quotas are per-org, so "make another org"
sidesteps them. These tests pin the two mitigations that don't need email or
captcha infrastructure — a kill switch and a dedicated tight rate limit.
"""
import uuid

from app.core.config import settings


def _payload():
    suffix = uuid.uuid4().hex[:8]
    return {
        "org_name": f"gate-{suffix}",
        "username": f"user_{suffix}",
        "email": f"user_{suffix}@example.com",
        "password": "Str0ngPass1",
    }


def test_signup_open_by_default(client):
    """Default behaviour is unchanged — registration still works."""
    r = client.post("/api/auth/register", json=_payload())
    assert r.status_code == 201, r.text
    assert "access_token" in r.json()


def test_closed_signup_is_refused(client, monkeypatch):
    monkeypatch.setattr(settings, "SIGNUPS_OPEN", False)
    r = client.post("/api/auth/register", json=_payload())
    assert r.status_code == 403
    assert "closed" in r.json()["detail"].lower()


def test_closed_signup_creates_no_org(client, monkeypatch):
    """A refused signup must not leave a half-created org/user behind."""
    payload = _payload()
    monkeypatch.setattr(settings, "SIGNUPS_OPEN", False)
    assert client.post("/api/auth/register", json=payload).status_code == 403

    # Re-open and confirm the username/email are still free (nothing was written).
    monkeypatch.setattr(settings, "SIGNUPS_OPEN", True)
    r = client.post("/api/auth/register", json=payload)
    assert r.status_code == 201, r.text


def test_closed_signup_does_not_block_existing_users(client, monkeypatch):
    """Closing signup gates registration only — existing users still log in."""
    payload = _payload()
    assert client.post("/api/auth/register", json=payload).status_code == 201

    monkeypatch.setattr(settings, "SIGNUPS_OPEN", False)
    r = client.post("/api/auth/login", json={
        "username": payload["username"], "password": payload["password"],
    })
    assert r.status_code == 200, r.text


def test_shipped_register_limit_is_tight_and_separate():
    """Guard the shipped defaults, not the test-env overrides.

    conftest raises every limit to keep multi-register test flows from tripping
    the limiter, so assert on the class defaults directly — that registration
    ships with its own, much tighter limit than login (per hour, not minute).
    """
    fields = type(settings).model_fields
    assert fields["RATE_LIMIT_REGISTER"].default == "5/hour"
    assert fields["RATE_LIMIT_LOGIN"].default.endswith("/minute")
