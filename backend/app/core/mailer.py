"""Outbound mail — a real SMTP transport behind a no-op default (issue #21).

`SMTP_HOST` unset is still the default and still sends nothing: dev, the test
suite and any self-hosted deployment that has not configured mail keep working
exactly as before, and the verification link goes to the log. Same shape as
`SENTRY_DSN` and the OTel endpoint. Don't "fix" mail appearing not to send
locally.

Set `SMTP_HOST` and mail is actually delivered. Only credentials are needed now;
the account is still the thing this repo does not have, so **the transport below
has never sent a message to a real server** — see `docs/GO_LIVE_CHECKLIST.md`
for the one-command check to run once an account exists.

Three rules the transport keeps, in order of how much they would cost to get
wrong:

* **Credentials never cross an unencrypted link.** If STARTTLS is disabled or
  the server does not offer it, a configured password means the send is
  abandoned, not attempted. A mail that does not go out is a support ticket; a
  leaked SMTP password is somebody sending phishing as you.
* **Nothing raises.** Registration has already committed by the time this runs,
  so a mail failure must not turn a successful signup into a 500.
* **The link is not logged once a transport exists.** The no-op path logs it
  because that *is* the delivery mechanism in dev. A verification link is a
  bearer credential for an account, and once mail really goes out, writing it to
  a log aggregator is a second, permanent copy nobody revokes.
"""
from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage

from app.core.config import settings

logger = logging.getLogger("datawhisper.mailer")

SUBJECT = "Verify your DataWhisper email address"

# Implicit TLS. Anything else is submission/plaintext plus STARTTLS.
_IMPLICIT_TLS_PORT = 465


def configured() -> bool:
    """True when a real transport is wired. False → send() only logs."""
    return bool(settings.SMTP_HOST)


def verification_link(token: str) -> str:
    """The URL a user follows to verify, or the bare token if no origin is set.

    `APP_BASE_URL` is empty by default because the backend cannot know the
    public origin of the SPA in front of it. Returning the token alone is
    honest — it is what a dev needs to complete the flow by hand — rather than
    inventing a localhost URL that may not be where the app is served.
    """
    base = settings.APP_BASE_URL.rstrip("/")
    return f"{base}/verify-email?token={token}" if base else token


def send_verification_email(email: str, token: str) -> bool:
    """Send the verification mail. Returns whether a real transport handled it.

    Never raises: a mail failure must not turn a successful registration into a
    500, because the account exists by then and the user can request a resend.
    """
    link = verification_link(token)
    if not configured():
        # INFO, not DEBUG: without a transport this line *is* the delivery
        # mechanism for local development, and it must not need log-level
        # tuning to appear. It is only reachable when no provider is set.
        logger.info(
            "mailer: no SMTP_HOST configured — not sending. Verification for %s: %s",
            email,
            link,
        )
        return False

    message = _build_message(email, link)
    try:
        _deliver(message)
    except Exception:  # noqa: BLE001 — see the docstring; callers must not fail
        # No link, no token, no password in this line — only who it was for.
        logger.exception("mailer: could not send verification mail to %s", email)
        return False
    logger.info("mailer: sent verification mail to %s", email)
    return True


def _build_message(email: str, link: str) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = SUBJECT
    message["From"] = settings.smtp_from
    message["To"] = email
    # Plain text only, deliberately: an HTML part would be a second place for
    # the link to be wrong, and this mail has one job.
    message.set_content(
        "Welcome to DataWhisper.\n\n"
        "Confirm this address to start asking questions of your data:\n\n"
        f"{link}\n\n"
        f"The link expires in {settings.EMAIL_VERIFICATION_TTL_HOURS} hours. "
        "If you did not create this account, ignore this message — nothing "
        "happens until the link is followed.\n"
    )
    return message


def _deliver(message: EmailMessage) -> None:
    """Open a connection, secure it, authenticate if asked, and send.

    Raises on any failure; `send_verification_email` owns the swallowing.
    """
    host, port = settings.SMTP_HOST, settings.SMTP_PORT
    timeout = settings.SMTP_TIMEOUT_SECONDS
    context = ssl.create_default_context()

    if port == _IMPLICIT_TLS_PORT:
        with smtplib.SMTP_SSL(host, port, timeout=timeout, context=context) as server:
            _authenticate(server, encrypted=True)
            server.send_message(message)
        return

    with smtplib.SMTP(host, port, timeout=timeout) as server:
        encrypted = False
        if settings.SMTP_STARTTLS:
            server.starttls(context=context)
            # A second EHLO is required after STARTTLS: the pre-TLS capability
            # list is not trustworthy and some servers advertise AUTH only after.
            server.ehlo()
            encrypted = True
        _authenticate(server, encrypted=encrypted)
        server.send_message(message)


def _authenticate(server, *, encrypted: bool) -> None:
    if not settings.SMTP_USERNAME:
        return
    if not encrypted:
        raise RuntimeError(
            "refusing to send SMTP credentials over an unencrypted connection — "
            "set SMTP_STARTTLS=true, or use port 465"
        )
    server.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
