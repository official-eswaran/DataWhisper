"""Signup captcha — the second half of issue #21.

`RATE_LIMIT_REGISTER` slows one IP and `SIGNUPS_OPEN=false` is all-or-nothing;
neither stops a distributed script minting orgs, and each org carries a free LLM
budget. These tests pin the verifier and the /register wiring.

The provider is never reached: `requests.post` is replaced throughout. What is
being tested is *our* behaviour on each answer the provider can give — including
the two that are easy to get backwards, a provider outage (must refuse) and an
unset secret (must not challenge at all).
"""
import uuid

import pytest
import requests

from app.core import captcha
from app.core.config import settings

HCAPTCHA_VERIFY = "https://api.hcaptcha.com/siteverify"


@pytest.fixture
def enabled(monkeypatch):
    """A fully configured hCaptcha deployment."""
    monkeypatch.setattr(settings, "CAPTCHA_PROVIDER", "hcaptcha")
    monkeypatch.setattr(settings, "CAPTCHA_SITE_KEY", "site-key-public")
    monkeypatch.setattr(settings, "CAPTCHA_SECRET", "secret-key-private")
    monkeypatch.setattr(settings, "CAPTCHA_VERIFY_URL", "")


class FakeResponse:
    def __init__(self, body, status_code=200):
        self._body = body
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


def _capture(monkeypatch, body, status_code=200):
    """Replace requests.post and record what the verifier sent."""
    sent = {}

    def fake_post(url, data=None, timeout=None):
        sent.update({"url": url, "data": data, "timeout": timeout})
        if isinstance(body, Exception):
            raise body
        return FakeResponse(body, status_code)

    monkeypatch.setattr(captcha.requests, "post", fake_post)
    return sent


def _payload(**extra):
    suffix = uuid.uuid4().hex[:8]
    return {
        "org_name": f"cap-{suffix}",
        "username": f"user_{suffix}",
        "email": f"user_{suffix}@example.com",
        "password": "Str0ngPass1",
        **extra,
    }


# ── The default: no provider, nothing changes ────────────────────────────────


def test_disabled_by_default():
    """An untouched deployment has no captcha at all."""
    assert captcha.configured() is False
    assert captcha.site_key() == ""


def test_disabled_verify_passes_without_calling_out(monkeypatch):
    """No provider → verify() is a no-op pass and makes no request.

    The no-request half matters: a stray call would make every test on a
    machine with network access depend on hCaptcha being up.
    """
    def explode(*a, **kw):  # pragma: no cover - must never run
        raise AssertionError("verify() called the network with no provider set")

    monkeypatch.setattr(captcha.requests, "post", explode)
    assert captcha.verify("") is True
    assert captcha.verify("anything") is True


def test_secret_without_known_provider_stays_off(monkeypatch):
    """An unknown provider disables the feature rather than half-enabling it."""
    monkeypatch.setattr(settings, "CAPTCHA_SECRET", "s")
    monkeypatch.setattr(settings, "CAPTCHA_PROVIDER", "recaptcha")
    assert captcha.configured() is False


def test_site_key_alone_does_not_enable(monkeypatch):
    """The secret is what switches it on — a widget nobody verifies is worse."""
    monkeypatch.setattr(settings, "CAPTCHA_SITE_KEY", "site-key-public")
    monkeypatch.setattr(settings, "CAPTCHA_SECRET", "")
    assert captcha.configured() is False
    assert captcha.site_key() == ""


# ── Verification ──────────────────────────────────────────────────────────────


def test_solved_challenge_passes(enabled, monkeypatch):
    _capture(monkeypatch, {"success": True})
    assert captcha.verify("solved-token") is True


def test_failed_challenge_is_refused(enabled, monkeypatch):
    _capture(monkeypatch, {"success": False, "error-codes": ["invalid-input-response"]})
    assert captcha.verify("bad-token") is False


def test_empty_token_is_refused_without_a_round_trip(enabled, monkeypatch):
    """No token is a definite failure; asking the provider wastes a call."""
    sent = _capture(monkeypatch, {"success": True})
    assert captcha.verify("") is False
    assert sent == {}


def test_request_shape_matches_the_siteverify_contract(enabled, monkeypatch):
    sent = _capture(monkeypatch, {"success": True})
    captcha.verify("solved-token")
    assert sent["url"] == HCAPTCHA_VERIFY
    assert sent["data"] == {"secret": "secret-key-private", "response": "solved-token"}
    assert sent["timeout"] == settings.CAPTCHA_TIMEOUT_SECONDS


def test_remote_ip_is_sent_only_when_given(enabled, monkeypatch):
    """Optional field: present when passed, absent — not empty — when not.

    Both providers treat an empty remoteip as a malformed value rather than an
    omission, so the key has to be missing entirely.
    """
    sent = _capture(monkeypatch, {"success": True})
    captcha.verify("t", remote_ip="203.0.113.7")
    assert sent["data"]["remoteip"] == "203.0.113.7"

    sent = _capture(monkeypatch, {"success": True})
    captcha.verify("t")
    assert "remoteip" not in sent["data"]


def test_turnstile_uses_its_own_endpoint(enabled, monkeypatch):
    """The second provider is a row in the table, not a second code path."""
    monkeypatch.setattr(settings, "CAPTCHA_PROVIDER", "turnstile")
    sent = _capture(monkeypatch, {"success": True})
    captcha.verify("t")
    assert sent["url"] == "https://challenges.cloudflare.com/turnstile/v0/siteverify"


def test_success_must_be_exactly_true(enabled, monkeypatch):
    """A truthy-but-not-True value is not a pass.

    Guards the shape of the check itself: `body["success"] is True` rather than
    a bare truthiness test, so a provider (or a proxy) answering with a string
    cannot be read as a solved challenge.
    """
    _capture(monkeypatch, {"success": "true"})
    assert captcha.verify("t") is False


# ── Unavailable ≠ failed: the direction that matters ─────────────────────────


@pytest.mark.parametrize(
    "failure",
    [
        requests.Timeout("timed out"),
        requests.ConnectionError("dns"),
        ValueError("not json"),
    ],
    ids=["timeout", "connection", "unparseable"],
)
def test_provider_failures_raise_rather_than_pass(enabled, monkeypatch, failure):
    """Every unknown answer refuses. None of them may return True."""
    _capture(monkeypatch, failure)
    with pytest.raises(captcha.CaptchaUnavailable):
        captcha.verify("t")


def test_provider_5xx_raises(enabled, monkeypatch):
    _capture(monkeypatch, {"success": True}, status_code=500)
    with pytest.raises(captcha.CaptchaUnavailable):
        captcha.verify("t")


def test_200_without_a_success_field_raises(enabled, monkeypatch):
    """A captive portal or proxy error page is 'unknown', not 'failed'."""
    _capture(monkeypatch, {"error-codes": ["nope"]})
    with pytest.raises(captcha.CaptchaUnavailable):
        captcha.verify("t")


def test_non_dict_body_raises(enabled, monkeypatch):
    _capture(monkeypatch, ["success"])
    with pytest.raises(captcha.CaptchaUnavailable):
        captcha.verify("t")


def test_plaintext_verify_url_is_refused(enabled, monkeypatch):
    """The secret never crosses an unencrypted link — the mailer's rule.

    Refused before the request is built, so the override cannot be used to
    downgrade the connection whatever it points at.
    """
    monkeypatch.setattr(settings, "CAPTCHA_VERIFY_URL", "http://api.hcaptcha.com/siteverify")
    sent = _capture(monkeypatch, {"success": True})
    with pytest.raises(captcha.CaptchaUnavailable):
        captcha.verify("t")
    assert sent == {}


def test_https_override_is_honoured(enabled, monkeypatch):
    monkeypatch.setattr(settings, "CAPTCHA_VERIFY_URL", "https://proxy.internal/siteverify")
    sent = _capture(monkeypatch, {"success": True})
    assert captcha.verify("t") is True
    assert sent["url"] == "https://proxy.internal/siteverify"


def test_neither_credential_is_logged(enabled, monkeypatch, caplog):
    """Not the secret, not the token — on any path, including the failures."""
    caplog.set_level("DEBUG", logger="datawhisper.captcha")

    _capture(monkeypatch, {"success": False, "error-codes": ["expired"]})
    captcha.verify("solved-token-abc")

    _capture(monkeypatch, requests.Timeout("timed out"))
    with pytest.raises(captcha.CaptchaUnavailable):
        captcha.verify("solved-token-abc")

    logged = caplog.text
    assert "solved-token-abc" not in logged
    assert "secret-key-private" not in logged
    assert "expired" in logged  # the useful half is kept


# ── Startup consistency check ────────────────────────────────────────────────


def test_no_warning_when_unset_or_complete(enabled):
    assert captcha.check_captcha_config() == []


def test_no_warning_on_a_default_deployment():
    assert captcha.check_captcha_config() == []


def test_secret_without_site_key_warns(monkeypatch):
    """The widget cannot render, so every signup fails closed and nothing says so."""
    monkeypatch.setattr(settings, "CAPTCHA_SECRET", "s")
    monkeypatch.setattr(settings, "CAPTCHA_SITE_KEY", "")
    warnings = captcha.check_captcha_config()
    assert len(warnings) == 1
    assert "CAPTCHA_SITE_KEY is empty" in warnings[0]


def test_site_key_without_secret_warns(monkeypatch):
    """The dangerous half: a challenge is shown and its answer never checked."""
    monkeypatch.setattr(settings, "CAPTCHA_SECRET", "")
    monkeypatch.setattr(settings, "CAPTCHA_SITE_KEY", "site-key-public")
    warnings = captcha.check_captcha_config()
    assert len(warnings) == 1
    assert "never checked" in warnings[0]


def test_unknown_provider_with_a_secret_warns(monkeypatch):
    monkeypatch.setattr(settings, "CAPTCHA_SECRET", "s")
    monkeypatch.setattr(settings, "CAPTCHA_SITE_KEY", "k")
    monkeypatch.setattr(settings, "CAPTCHA_PROVIDER", "recaptcha")
    warnings = captcha.check_captcha_config()
    assert len(warnings) == 1
    assert "signup is unprotected" in warnings[0]


# ── /register wiring ──────────────────────────────────────────────────────────


def test_register_is_unchanged_without_a_provider(client):
    """The default path: no captcha_token, still 201."""
    assert client.post("/api/auth/register", json=_payload()).status_code == 201


def test_register_refuses_a_missing_token_when_enabled(client, enabled, monkeypatch):
    """Omitting the field is not a bypass — that is the whole attack."""
    _capture(monkeypatch, {"success": True})
    r = client.post("/api/auth/register", json=_payload())
    assert r.status_code == 400
    assert "captcha" in r.json()["detail"].lower()


def test_register_accepts_a_solved_challenge(client, enabled, monkeypatch):
    sent = _capture(monkeypatch, {"success": True})
    r = client.post("/api/auth/register", json=_payload(captcha_token="solved"))
    assert r.status_code == 201, r.text
    assert sent["data"]["response"] == "solved"


def test_register_refuses_a_failed_challenge(client, enabled, monkeypatch):
    _capture(monkeypatch, {"success": False, "error-codes": ["invalid-input-response"]})
    r = client.post("/api/auth/register", json=_payload(captcha_token="bad"))
    assert r.status_code == 400


def test_provider_outage_refuses_the_signup_with_503(client, enabled, monkeypatch):
    """Fails closed, and says so distinctly.

    503 rather than 400 because the two need different things from the user —
    wait and retry, versus solve the challenge again — and the user has done
    nothing wrong here.
    """
    _capture(monkeypatch, requests.Timeout("timed out"))
    r = client.post("/api/auth/register", json=_payload(captcha_token="solved"))
    assert r.status_code == 503
    assert "try again" in r.json()["detail"].lower()


def test_a_refused_signup_creates_no_org(client, enabled, monkeypatch):
    """Nothing is written before the challenge is checked.

    Re-registering the same payload once the captcha passes must succeed, which
    it cannot if the refused attempt left the username or email taken.
    """
    payload = _payload(captcha_token="bad")
    _capture(monkeypatch, {"success": False})
    assert client.post("/api/auth/register", json=payload).status_code == 400

    _capture(monkeypatch, {"success": True})
    payload["captcha_token"] = "solved"
    assert client.post("/api/auth/register", json=payload).status_code == 201


def test_closed_signup_is_refused_before_the_provider_is_asked(client, enabled, monkeypatch):
    """Order matters: a closed deployment should not pay for captcha calls."""
    sent = _capture(monkeypatch, {"success": True})
    monkeypatch.setattr(settings, "SIGNUPS_OPEN", False)
    r = client.post("/api/auth/register", json=_payload(captcha_token="solved"))
    assert r.status_code == 403
    assert sent == {}


def test_oversized_token_is_rejected_by_validation(client, enabled, monkeypatch):
    """Bounded before anything is forwarded to the provider."""
    sent = _capture(monkeypatch, {"success": True})
    r = client.post("/api/auth/register", json=_payload(captcha_token="x" * 4001))
    assert r.status_code == 422
    assert sent == {}


# ── /signup-config ────────────────────────────────────────────────────────────


def test_signup_config_reports_no_captcha_by_default(client):
    body = client.get("/api/auth/signup-config").json()
    assert body["captcha"] is None
    assert body["signups_open"] is True


def test_signup_config_serves_the_site_key_when_enabled(client, enabled):
    body = client.get("/api/auth/signup-config").json()
    assert body["captcha"] == {"provider": "hcaptcha", "site_key": "site-key-public"}


def test_signup_config_never_serves_the_secret(client, enabled):
    """The one thing this endpoint must never leak."""
    assert "secret-key-private" not in client.get("/api/auth/signup-config").text


def test_signup_config_needs_no_authentication(client, enabled):
    """It is read by the signup page, which by definition has no session."""
    assert client.get("/api/auth/signup-config").status_code == 200
