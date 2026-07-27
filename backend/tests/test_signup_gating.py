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


# ── 422 responses must not echo what was submitted ────────────────────────────
# Pydantic's errors() carries an "input" key holding the rejected value, so
# returning it verbatim put the submitted password in the response body of every
# failed registration — visible to proxy logs, devtools, and any error tracker
# capturing HTTP bodies.

def test_validation_errors_never_echo_the_submitted_password(client):
    # Long enough to pass the length rule but with no digit, so the validator
    # rejects it and the handler has something to (not) echo back.
    secret = "DoNotLeakThisSecret"
    payload = _payload() | {"password": secret}
    r = client.post("/api/auth/register", json=payload)

    assert r.status_code == 422, r.text
    assert secret not in r.text, "the submitted password came back in the 422 body"


def test_validation_errors_expose_no_input_or_ctx_keys(client):
    r = client.post("/api/auth/register", json=_payload() | {"password": "short"})
    assert r.status_code == 422
    for err in r.json()["errors"]:
        assert set(err) == {"loc", "msg", "type"}, f"unexpected keys leaked: {err}"


def test_validation_errors_still_say_which_field_and_why(client):
    """Sanitising must not cost the client the information it needs."""
    r = client.post("/api/auth/register", json=_payload() | {"password": "nodigitshere"})
    assert r.status_code == 422
    err = r.json()["errors"][0]
    assert err["loc"][-1] == "password"
    # And the message is shown as written, without Pydantic's "Value error, ".
    assert err["msg"] == "Password must contain a digit"


def test_login_validation_does_not_echo_the_password(client):
    """The same handler covers login, where the leak would be just as bad."""
    secret = "x" * 200  # over the 128-char cap, so the validator rejects it
    r = client.post("/api/auth/login", json={"username": "ceo", "password": secret})
    assert r.status_code == 422
    assert secret not in r.text
