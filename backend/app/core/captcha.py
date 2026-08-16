"""Signup captcha — a real verifier behind a no-op default (issue #21).

`CAPTCHA_SECRET` unset is the default and verifies nothing: dev, the test suite
and every self-hosted deployment that has not configured a provider keep working
exactly as before. Same shape as `SMTP_HOST`, `SENTRY_DSN` and the OTel
endpoint. Don't "fix" the captcha appearing absent locally.

Set a provider's key pair and `/api/auth/register` will not create an
organization without a solved challenge. This is the other half of the #21
mitigation: `RATE_LIMIT_REGISTER` only slows one IP and `SIGNUPS_OPEN=false` is
all-or-nothing, so neither stops a distributed script from minting orgs — each
of which carries a free LLM budget.

**No challenge has ever been solved against a real provider from this repo**;
that needs an hCaptcha or Turnstile account. See `docs/GO_LIVE_CHECKLIST.md`.

Both supported providers expose the same siteverify contract — POST
`secret`/`response`/`remoteip` as a form, receive `{"success": bool,
"error-codes": [...]}` — so one implementation covers them and a third provider
with that shape needs only a row in `PROVIDERS`.

Four rules this keeps, in order of how much they would cost to get wrong:

* **An unverifiable signup is refused, not allowed.** If the provider times out
  or answers with a 500, registration fails with a retryable 503. A control that
  opens when it cannot be evaluated is not a control — and "the captcha endpoint
  is unreachable" is precisely the state an attacker would manufacture if
  failing open bought them unlimited orgs.
* **The secret never crosses an unencrypted link.** A non-https verify URL is
  refused outright rather than posted to, mirroring the mailer's refusal to
  authenticate without TLS. Leaking this key lets anyone verify their own
  challenges, which is the whole control.
* **Nothing about the challenge reaches the log.** Not the secret, not the
  response token — the token is a bearer credential for one solved challenge,
  and provider error codes are enough to debug with.
* **Failing the challenge and being unable to ask are different answers.** They
  reach the user as different statuses because they need different things from
  them: solve it again, versus try again shortly.
"""
from __future__ import annotations

import logging

import requests

from app.core.config import settings

logger = logging.getLogger("datawhisper.captcha")


class CaptchaUnavailable(RuntimeError):
    """The provider could not be asked. Distinct from a failed challenge.

    Raised for timeouts, connection errors, non-200s and unparseable bodies —
    everything where the honest answer is "unknown". Callers turn this into a
    retryable failure; they must never turn it into a pass.
    """


# provider → siteverify endpoint. Adding a provider that speaks this contract is
# one row here plus one in the frontend's script map.
#
# The frontend deliberately holds its own provider → widget-script map rather
# than being told a URL to inject by this endpoint: a misconfigured or
# compromised API should not be able to make the SPA load arbitrary JavaScript.
PROVIDERS: dict[str, str] = {
    "hcaptcha": "https://api.hcaptcha.com/siteverify",
    "turnstile": "https://challenges.cloudflare.com/turnstile/v0/siteverify",
}


def configured() -> bool:
    """True when a real provider is wired. False → verify() is a no-op pass.

    Keyed on the *secret*, not the site key: the site key is public and only
    renders a widget, while the secret is what makes verification mean
    anything. A deployment with only a site key set would show a challenge and
    accept any answer, which is worse than showing none.
    """
    return bool(settings.CAPTCHA_SECRET) and settings.CAPTCHA_PROVIDER in PROVIDERS


def site_key() -> str:
    """The public key the widget renders with. Empty when not configured."""
    return settings.CAPTCHA_SITE_KEY if configured() else ""


def check_captcha_config() -> list[str]:
    """Warn at startup about key pairs that are half-set. Returns the warnings.

    Every combination below *runs*, which is the problem: each one either shuts
    signup down or leaves it unprotected while looking configured, and nothing
    else in the system would say so. Same job as `check_limits_are_reachable`
    for the row ceilings.
    """
    warnings: list[str] = []
    provider, site, secret = (
        settings.CAPTCHA_PROVIDER,
        settings.CAPTCHA_SITE_KEY,
        settings.CAPTCHA_SECRET,
    )

    if secret and provider not in PROVIDERS:
        warnings.append(
            f"CAPTCHA_SECRET is set but CAPTCHA_PROVIDER={provider!r} is not one of "
            f"{sorted(PROVIDERS)} — the captcha is OFF and signup is unprotected."
        )
    elif secret and not site:
        # The widget cannot render without its public key, so no legitimate
        # signup can produce a token — and the server refuses tokenless
        # signups. Registration is closed and nothing says so.
        warnings.append(
            "CAPTCHA_SECRET is set but CAPTCHA_SITE_KEY is empty — the widget "
            "cannot render, so every signup will be refused. Set both or neither."
        )
    elif site and not secret:
        # The inverse, and the more dangerous one: it looks protected.
        warnings.append(
            "CAPTCHA_SITE_KEY is set but CAPTCHA_SECRET is empty — a challenge "
            "will be shown and its answer never checked. Set both or neither."
        )

    for warning in warnings:
        logger.warning("captcha: %s", warning)
    return warnings


def verify(token: str, remote_ip: str | None = None) -> bool:
    """Ask the provider whether `token` is a solved challenge.

    Returns True/False for a definite answer. Raises `CaptchaUnavailable` when
    there is no answer to be had — never returns True on an error path.
    """
    if not configured():
        return True

    if not token:
        # Not an error: the absence of a token is a definite failure, and going
        # to the provider to be told so wastes a round trip on every bot.
        return False

    verify_url = settings.CAPTCHA_VERIFY_URL or PROVIDERS[settings.CAPTCHA_PROVIDER]
    if not verify_url.startswith("https://"):
        # Refused rather than downgraded. The alternative is posting the secret
        # in cleartext, and an override exists only so a test double or an
        # enterprise proxy can stand in — neither needs plaintext.
        raise CaptchaUnavailable(f"captcha verify URL is not https: {verify_url}")

    payload = {"secret": settings.CAPTCHA_SECRET, "response": token}
    if remote_ip:
        payload["remoteip"] = remote_ip

    try:
        response = requests.post(
            verify_url, data=payload, timeout=settings.CAPTCHA_TIMEOUT_SECONDS
        )
        response.raise_for_status()
        body = response.json()
    except Exception as exc:  # noqa: BLE001 — every failure means "unknown"
        # No secret and no token in this line; the exception carries the URL and
        # status, which is what a misconfiguration needs.
        logger.warning("captcha: could not verify with %s (%s)", verify_url, exc)
        raise CaptchaUnavailable(str(exc)) from exc

    if not isinstance(body, dict) or "success" not in body:
        # A 200 whose body is not the documented contract is an unknown answer,
        # not a failure — most often a captive portal or proxy error page.
        logger.warning("captcha: unexpected response body from %s", verify_url)
        raise CaptchaUnavailable("captcha response had no success field")

    success = body["success"] is True
    if not success:
        # Error codes only. They name the *reason* (expired, already-seen,
        # bad-secret) without carrying either credential.
        logger.info("captcha: challenge rejected — %s", body.get("error-codes"))
    return success
