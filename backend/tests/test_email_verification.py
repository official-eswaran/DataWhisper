"""Email verification gate on queries (issue #21).

The signup rate limit and ``SIGNUPS_OPEN`` only slow single-IP abuse; quotas are
per-org, so "register another org" still buys another free LLM budget. Requiring
a confirmed address before *queries run* puts a per-identity cost on that path.

Two properties are worth stating up front, because they are what the tests are
mostly about:

* The gate is **per-org, keyed on the owner**. Checking each user individually
  would leave a hole — an unverified owner could create a member through the
  admin route and query as them.
* The gate is **off under DEBUG**, so dev and the rest of this suite are
  unaffected. Every test here that wants the gate on turns it on explicitly.
"""
import smtplib
import uuid

import pytest

from app.core.config import settings
from app.core.database import (
    consume_email_verification_token,
    create_email_verification_token,
    create_user_in_org,
    is_email_verified,
    is_org_email_verified,
)
from app.core.security import hash_password


def _payload():
    suffix = uuid.uuid4().hex[:8]
    return {
        "org_name": f"verify-{suffix}",
        "username": f"user_{suffix}",
        "email": f"user_{suffix}@example.com",
        "password": "Str0ngPass1",
    }


@pytest.fixture
def gate_on(monkeypatch):
    """Turn the gate on regardless of DEBUG."""
    monkeypatch.setattr(settings, "REQUIRE_EMAIL_VERIFICATION", True)


def _register(client):
    payload = _payload()
    r = client.post("/api/auth/register", json=payload)
    assert r.status_code == 201, r.text
    return payload, r.json()


def _ask(client, token):
    """Hit the query route. Returns the response.

    The session_id is a well-formed UUID that does not exist, which is the point:
    the gate is a dependency, so it fires *before* the route body ever looks the
    session up. A 403 here therefore means the gate refused, and a 404 means it
    let the request through.
    """
    return client.post(
        "/api/query/",
        json={"session_id": str(uuid.uuid4()), "question": "how many rows are there?"},
        headers={"Authorization": f"Bearer {token}"},
    )


# ── The gate ─────────────────────────────────────────────────────────────────


def test_unverified_org_is_refused_at_query(client, gate_on):
    _, tokens = _register(client)
    r = _ask(client, tokens["access_token"])
    assert r.status_code == 403, r.text
    assert "email" in r.json()["detail"].lower()


def test_refusal_says_how_to_recover(client, gate_on):
    """A 403 that doesn't tell you what to do is a dead end."""
    _, tokens = _register(client)
    detail = _ask(client, tokens["access_token"]).json()["detail"]
    assert "resend" in detail.lower()


def test_verified_org_passes_the_gate(client, gate_on):
    payload, tokens = _register(client)
    token = create_email_verification_token(payload["username"], 24)
    assert consume_email_verification_token(token) == payload["username"]

    r = _ask(client, tokens["access_token"])
    # 404 = the gate let it through and the (nonexistent) session was looked up.
    assert r.status_code == 404, r.text


def test_gate_is_off_under_debug_by_default(client):
    """The whole suite depends on this: no fixture, no verification, no 403."""
    _, tokens = _register(client)
    assert _ask(client, tokens["access_token"]).status_code == 404


def test_seeded_demo_accounts_are_verified(client, gate_on, admin_token):
    """`ceo`/`manager` have no real mailbox — they must not be gated out."""
    assert is_email_verified("ceo")
    assert _ask(client, admin_token).status_code == 404


# ── Per-org, not per-user ─────────────────────────────────────────────────────


def test_member_of_an_unverified_org_is_also_refused(client, gate_on):
    """The hole a per-user check would leave.

    An unverified owner creates a member through the admin route; that member is
    marked verified (an admin vouched for them). If the gate read the *user's*
    flag, the member would sail through and the owner would just use that
    account. It reads the org's owner instead.
    """
    payload, tokens = _register(client)
    member = f"member_{uuid.uuid4().hex[:8]}"
    org_id = _org_id_of(payload["username"])
    create_user_in_org(
        org_id=org_id,
        username=member,
        email=f"{member}@example.com",
        password_hash=hash_password("Str0ngPass1"),
        role="member",
    )
    # The member's own flag is set...
    assert is_email_verified(member)
    # ...but the org is still unverified, so nobody in it may query.
    assert not is_org_email_verified(org_id)

    login = client.post(
        "/api/auth/login", json={"username": member, "password": "Str0ngPass1"}
    )
    assert login.status_code == 200, login.text
    assert _ask(client, login.json()["access_token"]).status_code == 403


def _org_id_of(username: str) -> int:
    from app.core.database import get_user_by_username

    return get_user_by_username(username)["org_id"]


def test_verifying_the_owner_unblocks_the_whole_org(client, gate_on):
    payload, _ = _register(client)
    org_id = _org_id_of(payload["username"])
    assert not is_org_email_verified(org_id)

    consume_email_verification_token(
        create_email_verification_token(payload["username"], 24)
    )
    assert is_org_email_verified(org_id)


# ── Token lifecycle ───────────────────────────────────────────────────────────


def test_token_is_single_use(client):
    payload, _ = _register(client)
    token = create_email_verification_token(payload["username"], 24)

    assert consume_email_verification_token(token) == payload["username"]
    # Replaying a link from an old mail must not work a second time.
    assert consume_email_verification_token(token) is None


def test_expired_token_is_rejected(client):
    payload, _ = _register(client)
    token = create_email_verification_token(payload["username"], -1)  # already past
    assert consume_email_verification_token(token) is None
    assert not is_email_verified(payload["username"])


def test_unknown_token_is_rejected(client):
    assert consume_email_verification_token("not-a-real-token") is None


def test_resend_invalidates_the_previous_token(client):
    """Otherwise every resend leaves another live link behind."""
    payload, _ = _register(client)
    first = create_email_verification_token(payload["username"], 24)
    second = create_email_verification_token(payload["username"], 24)

    assert consume_email_verification_token(first) is None
    assert consume_email_verification_token(second) == payload["username"]


def test_plaintext_token_is_not_stored(client):
    """Only the SHA-256 is persisted, for the same reason passwords are hashed."""
    from sqlalchemy import select

    from app.core.database import email_verification_tokens, get_engine

    payload, _ = _register(client)
    token = create_email_verification_token(payload["username"], 24)

    with get_engine().connect() as conn:
        stored = conn.execute(
            select(email_verification_tokens.c.token_hash).where(
                email_verification_tokens.c.username == payload["username"]
            )
        ).scalars().all()

    assert stored, "no token row was written"
    assert token not in stored


# ── Endpoints ─────────────────────────────────────────────────────────────────


def test_verify_endpoint_accepts_a_valid_token(client):
    payload, _ = _register(client)
    token = create_email_verification_token(payload["username"], 24)

    r = client.post("/api/auth/verify-email", json={"token": token})
    assert r.status_code == 200, r.text
    assert r.json()["username"] == payload["username"]
    assert is_email_verified(payload["username"])


def test_verify_endpoint_rejects_a_bad_token(client):
    r = client.post("/api/auth/verify-email", json={"token": "nonsense"})
    assert r.status_code == 400
    assert "invalid" in r.json()["detail"].lower()


def test_resend_does_not_reveal_whether_an_account_exists(client):
    """Anything else turns this into an account-enumeration oracle — the exact
    leak /login goes to trouble to avoid."""
    payload, _ = _register(client)

    known = client.post(
        "/api/auth/verify-email/resend", json={"username": payload["username"]}
    )
    unknown = client.post(
        "/api/auth/verify-email/resend", json={"username": "no_such_user_here"}
    )

    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json()


def test_resend_issues_a_working_token(client, gate_on, monkeypatch):
    """The recovery path a 403 points at has to actually recover.

    The token is captured at the mailer, which is where a real transport would
    see it — so this exercises the same handoff production would use rather than
    minting a token directly.
    """
    payload, tokens = _register(client)
    assert _ask(client, tokens["access_token"]).status_code == 403

    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "app.api.routes.auth.send_verification_email",
        lambda email, token: sent.append((email, token)) or True,
    )

    r = client.post(
        "/api/auth/verify-email/resend", json={"username": payload["username"]}
    )
    assert r.status_code == 200
    assert len(sent) == 1
    assert sent[0][0] == payload["email"]

    assert client.post(
        "/api/auth/verify-email", json={"token": sent[0][1]}
    ).status_code == 200
    assert _ask(client, tokens["access_token"]).status_code == 404


def test_resend_sends_nothing_for_an_already_verified_account(client, monkeypatch):
    payload, _ = _register(client)
    consume_email_verification_token(
        create_email_verification_token(payload["username"], 24)
    )

    sent = []
    monkeypatch.setattr(
        "app.api.routes.auth.send_verification_email",
        lambda email, token: sent.append(token) or True,
    )
    client.post("/api/auth/verify-email/resend", json={"username": payload["username"]})
    assert sent == []


def test_registration_survives_a_failing_mailer(client, monkeypatch):
    """The account exists by the time mail is attempted. A transport outage must
    not turn that into a 500 the user reads as "signup failed" — /resend is the
    recovery path."""
    monkeypatch.setattr(
        "app.api.routes.auth.send_verification_email",
        lambda email, token: (_ for _ in ()).throw(RuntimeError("smtp down")),
    )
    r = client.post("/api/auth/register", json=_payload())
    assert r.status_code == 201, r.text


# ── Registration/login responses ──────────────────────────────────────────────


def test_register_still_succeeds_and_returns_a_session(client, gate_on):
    """The gate is on queries, not on the account. Blocking registration would
    only move the dead end earlier — the user needs a session to reach the UI
    that tells them to check their mail."""
    _, tokens = _register(client)
    assert tokens["access_token"]
    assert tokens["email_verified"] is False


def test_login_reports_verification_state(client, gate_on):
    payload, _ = _register(client)
    r = client.post(
        "/api/auth/login",
        json={"username": payload["username"], "password": payload["password"]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["email_verified"] is False

    consume_email_verification_token(
        create_email_verification_token(payload["username"], 24)
    )
    r = client.post(
        "/api/auth/login",
        json={"username": payload["username"], "password": payload["password"]},
    )
    assert r.json()["email_verified"] is True


def test_login_reports_verified_when_the_gate_is_off(client):
    """With the gate off, nothing is withheld, so nothing should claim to be."""
    payload, _ = _register(client)
    r = client.post(
        "/api/auth/login",
        json={"username": payload["username"], "password": payload["password"]},
    )
    assert r.json()["email_verified"] is True


# ── Mailer ────────────────────────────────────────────────────────────────────


def test_mailer_is_a_noop_without_a_transport(monkeypatch, caplog):
    from app.core import mailer

    monkeypatch.setattr(settings, "SMTP_HOST", "")
    assert mailer.configured() is False
    with caplog.at_level("INFO", logger="datawhisper.mailer"):
        assert mailer.send_verification_email("a@example.com", "tok123") is False
    # Without a transport this log line *is* the delivery mechanism in dev.
    assert "tok123" in caplog.text


# ── The SMTP transport (#21) ──────────────────────────────────────────────────
#
# `SMTP_HOST` unset is still a no-op; the tests above cover that. These cover
# the transport that runs when an operator does configure one. Nothing here
# opens a socket: `smtplib` is replaced with a recorder, which is also what lets
# the credential and TLS assertions be made at all.


class _FakeSMTP:
    """Records what the mailer did to it, and can be told to fail on cue."""

    instances: list["_FakeSMTP"] = []

    def __init__(self, host, port, timeout=None, context=None):
        self.host, self.port, self.timeout, self.context = host, port, timeout, context
        self.calls: list[str] = []
        self.logged_in: tuple[str, str] | None = None
        self.sent: list[object] = []
        self.starttls_fails = False
        _FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.calls.append("close")
        return False

    def starttls(self, context=None):
        self.calls.append("starttls")
        if self.starttls_fails:
            raise smtplib.SMTPNotSupportedError("STARTTLS not supported")

    def ehlo(self):
        self.calls.append("ehlo")

    def login(self, username, password):
        self.calls.append("login")
        self.logged_in = (username, password)

    def send_message(self, message):
        self.calls.append("send")
        self.sent.append(message)


@pytest.fixture
def smtp(monkeypatch):
    """Configure a transport and capture it. Yields the class, not an instance —
    the mailer constructs its own, and which class it picked is an assertion."""
    _FakeSMTP.instances = []
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(settings, "SMTP_PORT", 587)
    monkeypatch.setattr(settings, "SMTP_USERNAME", "postmaster@example.com")
    monkeypatch.setattr(settings, "SMTP_PASSWORD", "hunter2")
    monkeypatch.setattr(settings, "SMTP_FROM", "noreply@example.com")
    monkeypatch.setattr(settings, "SMTP_STARTTLS", True)
    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", _FakeSMTP)
    return _FakeSMTP


def test_a_configured_transport_actually_sends(smtp, monkeypatch):
    from app.core import mailer

    monkeypatch.setattr(settings, "APP_BASE_URL", "https://app.example.com")
    assert mailer.send_verification_email("user@example.com", "tok123") is True

    (server,) = smtp.instances
    assert server.calls == ["starttls", "ehlo", "login", "send", "close"]
    assert (server.host, server.port) == ("smtp.example.com", 587)


def test_the_message_carries_the_link_and_the_right_headers(smtp, monkeypatch):
    from app.core import mailer

    monkeypatch.setattr(settings, "APP_BASE_URL", "https://app.example.com")
    mailer.send_verification_email("user@example.com", "tok123")

    (message,) = smtp.instances[0].sent
    assert message["To"] == "user@example.com"
    assert message["From"] == "noreply@example.com"
    assert "https://app.example.com/verify-email?token=tok123" in message.get_content()


def test_the_from_address_falls_back_to_the_username(smtp, monkeypatch):
    """Providers reject mail whose From they do not own, and the username is the
    address they do."""
    from app.core import mailer

    monkeypatch.setattr(settings, "SMTP_FROM", "")
    mailer.send_verification_email("user@example.com", "tok")
    assert smtp.instances[0].sent[0]["From"] == "postmaster@example.com"


def test_implicit_tls_is_used_on_port_465(smtp, monkeypatch):
    from app.core import mailer

    monkeypatch.setattr(settings, "SMTP_PORT", 465)
    assert mailer.send_verification_email("user@example.com", "tok") is True
    # No STARTTLS: the connection was already encrypted.
    assert smtp.instances[0].calls == ["login", "send", "close"]


def test_credentials_are_never_sent_unencrypted(smtp, monkeypatch):
    """The assertion that matters most here. A mail that does not go out is a
    support ticket; a leaked SMTP password is somebody phishing as you."""
    from app.core import mailer

    monkeypatch.setattr(settings, "SMTP_STARTTLS", False)
    assert mailer.send_verification_email("user@example.com", "tok") is False

    server = smtp.instances[0]
    assert server.logged_in is None
    assert "login" not in server.calls
    assert "send" not in server.calls


def test_a_server_that_cannot_start_tls_gets_no_credentials(smtp):
    """Same rule, arrived at from the server's side rather than config."""
    from app.core import mailer

    original_init = _FakeSMTP.__init__

    def failing_init(self, *a, **kw):
        original_init(self, *a, **kw)
        self.starttls_fails = True

    _FakeSMTP.__init__ = failing_init
    try:
        assert mailer.send_verification_email("user@example.com", "tok") is False
    finally:
        _FakeSMTP.__init__ = original_init
    assert smtp.instances[0].logged_in is None


def test_an_unauthenticated_relay_still_sends(smtp, monkeypatch):
    """No username means no credentials to protect, so plaintext is the
    operator's call — an internal relay is a normal deployment."""
    from app.core import mailer

    monkeypatch.setattr(settings, "SMTP_USERNAME", "")
    monkeypatch.setattr(settings, "SMTP_STARTTLS", False)
    assert mailer.send_verification_email("user@example.com", "tok") is True
    assert smtp.instances[0].calls == ["send", "close"]


def test_a_broken_server_does_not_raise(smtp, monkeypatch):
    """Registration has already committed; a mail failure must not 500 it."""
    from app.core import mailer

    def explode(self, message):
        raise smtplib.SMTPRecipientsRefused({"user@example.com": (550, b"nope")})

    monkeypatch.setattr(_FakeSMTP, "send_message", explode)
    assert mailer.send_verification_email("user@example.com", "tok") is False


def test_an_unreachable_server_does_not_raise(monkeypatch):
    from app.core import mailer

    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.com")

    def refuse(*a, **kw):
        raise OSError("connection refused")

    monkeypatch.setattr(smtplib, "SMTP", refuse)
    assert mailer.send_verification_email("user@example.com", "tok") is False


def test_the_send_is_bounded_by_a_timeout(smtp, monkeypatch):
    """Registration blocks on this call, so an unreachable server must not hang
    the signup for longer than the configured budget."""
    from app.core import mailer

    monkeypatch.setattr(settings, "SMTP_TIMEOUT_SECONDS", 3.5)
    mailer.send_verification_email("user@example.com", "tok")
    assert smtp.instances[0].timeout == 3.5


def test_the_link_is_not_logged_once_a_transport_exists(smtp, monkeypatch, caplog):
    """A verification link is a bearer credential for the account. Logging it is
    fine when the log *is* the delivery mechanism, and a second permanent copy
    once it is not."""
    from app.core import mailer

    monkeypatch.setattr(settings, "APP_BASE_URL", "https://app.example.com")
    with caplog.at_level("DEBUG", logger="datawhisper.mailer"):
        mailer.send_verification_email("user@example.com", "tok123")
    assert "tok123" not in caplog.text
    assert "user@example.com" in caplog.text


def test_a_failure_does_not_log_the_link_either(smtp, monkeypatch, caplog):
    from app.core import mailer

    def explode(self, message):
        raise smtplib.SMTPServerDisconnected("gone")

    monkeypatch.setattr(_FakeSMTP, "send_message", explode)
    monkeypatch.setattr(settings, "APP_BASE_URL", "https://app.example.com")
    with caplog.at_level("DEBUG", logger="datawhisper.mailer"):
        mailer.send_verification_email("user@example.com", "tok123")
    assert "tok123" not in caplog.text


def test_the_password_is_never_logged(smtp, monkeypatch, caplog):
    from app.core import mailer

    def explode(self, message):
        raise smtplib.SMTPServerDisconnected("gone")

    monkeypatch.setattr(_FakeSMTP, "send_message", explode)
    with caplog.at_level("DEBUG", logger="datawhisper.mailer"):
        mailer.send_verification_email("user@example.com", "tok")
    assert "hunter2" not in caplog.text


def test_verification_link_uses_the_configured_origin(monkeypatch):
    from app.core import mailer

    monkeypatch.setattr(settings, "APP_BASE_URL", "https://app.example.com/")
    assert mailer.verification_link("abc") == "https://app.example.com/verify-email?token=abc"


def test_verification_link_falls_back_to_the_bare_token(monkeypatch):
    """The backend can't know the SPA's public origin; inventing one would be
    worse than handing back the token a dev can paste."""
    from app.core import mailer

    monkeypatch.setattr(settings, "APP_BASE_URL", "")
    assert mailer.verification_link("abc") == "abc"


# ── Settings ──────────────────────────────────────────────────────────────────


def test_requirement_defaults_to_on_in_production_off_in_dev(monkeypatch):
    monkeypatch.setattr(settings, "REQUIRE_EMAIL_VERIFICATION", None)
    monkeypatch.setattr(settings, "DEBUG", True)
    assert settings.should_require_email_verification is False
    monkeypatch.setattr(settings, "DEBUG", False)
    assert settings.should_require_email_verification is True


def test_explicit_setting_overrides_the_debug_default(monkeypatch):
    monkeypatch.setattr(settings, "DEBUG", True)
    monkeypatch.setattr(settings, "REQUIRE_EMAIL_VERIFICATION", True)
    assert settings.should_require_email_verification is True
    monkeypatch.setattr(settings, "DEBUG", False)
    monkeypatch.setattr(settings, "REQUIRE_EMAIL_VERIFICATION", False)
    assert settings.should_require_email_verification is False
