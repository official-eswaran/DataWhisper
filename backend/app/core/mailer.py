"""Outbound mail — an interface with a no-op transport (issue #21).

Deliberately no network client. `SMTP_HOST` is unset in dev, test and the
current deployment, so `send` logs and returns; the same shape as `SENTRY_DSN`
and `OTEL_EXPORTER_OTLP_ENDPOINT`, which are also inert until configured. Don't
"fix" mail appearing not to send locally.

The point of the seam is that the *verification flow* can be built, tested and
enforced now, and the provider dropped in later without touching the callers —
wiring a real SMTP/API transport is the deferred half of issue #21 and needs an
account this repo doesn't have. `configured` is what tells an operator which
half they're running.
"""
from __future__ import annotations

import logging

from app.core.config import settings

logger = logging.getLogger("datawhisper.mailer")


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

    # No provider is wired yet (deferred half of #21). Reaching here means an
    # operator set SMTP_HOST and expects mail to go out, so this is a loud
    # failure rather than a silent success that strands users unverified.
    logger.error(
        "mailer: SMTP_HOST is set but no transport is implemented — "
        "verification mail to %s was NOT sent. See issue #21.",
        email,
    )
    return False
