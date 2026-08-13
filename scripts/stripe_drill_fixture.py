#!/usr/bin/env python3
"""Drive and check the Stripe money path end to end (issue #19).

Run from ``backend/`` with the app importable::

    cd backend && PYTHONPATH=. python3 ../scripts/stripe_drill_fixture.py webhooks

``stripe_drill.sh`` is the entry point. Two independent halves:

``webhooks``
    Needs **no Stripe account**. Signs event payloads with the real signing
    scheme and POSTs them to a running app over HTTP, then checks what the plan
    actually did. The existing suite feeds events straight to ``handle_event``
    with signature verification stubbed (`test_billing.py`, line 4), so until
    this existed **no test had ever produced a valid signature or exercised the
    raw-body path** — the two things `BILLING.md` warns hardest about.

``api``
    Needs either stripe-mock (``STRIPE_API_BASE``) or real test-mode keys.
    Calls ``Customer.create``, ``checkout.Session.create`` and
    ``billing_portal.Session.create`` through the real SDK, so the request
    shapes are validated by something other than our own stubs.

Every check prints PASS/FAIL and the script exits non-zero if any failed, so
this is a pass/fail gate rather than something to read carefully.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.request

ORG_SLUG = "stripe-drill"
ORG_NAME = "Stripe Drill Org"
USERNAME = "stripe-drill-owner"
EMAIL = "stripe-drill@example.invalid"
PRICE_PRO = "price_drill_pro"

_failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{f' — {detail}' if detail else ''}")
    if not ok:
        _failures.append(name)
    return ok


def finish() -> None:
    if _failures:
        print(f"\n{len(_failures)} check(s) failed: {', '.join(_failures)}", file=sys.stderr)
        raise SystemExit(1)
    print("\nall checks passed")


# ── Fixture org ───────────────────────────────────────────────────────────────

def seed_org() -> int:
    from sqlalchemy import insert, select

    from app.core.database import GENESIS_HASH, audit_chain_state, get_engine, organizations, users
    from app.core.security import hash_password

    with get_engine().begin() as conn:
        existing = conn.execute(
            select(organizations.c.id).where(organizations.c.slug == ORG_SLUG)
        ).scalar()
        if existing is not None:
            return existing
        org_id = conn.execute(
            insert(organizations).values(name=ORG_NAME, slug=ORG_SLUG, plan="free")
        ).inserted_primary_key[0]
        conn.execute(insert(audit_chain_state).values(org_id=org_id, last_hash=GENESIS_HASH))
        conn.execute(
            insert(users).values(
                org_id=org_id, username=USERNAME, email=EMAIL,
                password_hash=hash_password("drill-only-not-a-real-secret"),
                role="owner", email_verified=True,
            )
        )
    return org_id


def org_state(org_id: int) -> dict:
    from app.core.database import get_org_billing

    return get_org_billing(org_id)


# ── Signed webhook delivery ───────────────────────────────────────────────────

def sign(payload: bytes, secret: str, timestamp: int | None = None) -> str:
    """Build a Stripe-Signature header the same way Stripe does.

    HMAC-SHA256 over ``{timestamp}.{raw body}``. Written out rather than taken
    from the SDK on purpose: a helper that generated signatures with the same
    code that verifies them would agree with itself no matter what either did.
    """
    ts = timestamp or int(time.time())
    signed = f"{ts}.".encode() + payload
    digest = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return f"t={ts},v1={digest}"


def post_event(base_url: str, event: dict, secret: str, *, corrupt: str = "") -> tuple[int, str]:
    """POST a signed event. ``corrupt`` breaks it deliberately: 'signature' or 'body'."""
    payload = json.dumps(event).encode()
    header = sign(payload, "whsec_wrong_secret" if corrupt == "signature" else secret)
    if corrupt == "body":
        # Signature is valid for the original bytes; the bytes then change.
        payload = payload.replace(b'"status"', b'"sTaTus"')
    req = urllib.request.Request(
        f"{base_url}/api/billing/webhook", data=payload,
        headers={"Content-Type": "application/json", "Stripe-Signature": header},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def subscription_event(event_id: str, event_type: str, org_id: int, status: str) -> dict:
    """A payload shaped like one Stripe actually sends.

    The ``object`` discriminators and the envelope fields are not decoration:
    the SDK needs them to build a typed Event, and without them
    ``construct_event`` raises before signature checking even matters. The unit
    suite never noticed, because it hands dicts straight to ``handle_event``
    and stubs verification out — which is precisely the gap this drill fills.
    """
    return {
        "id": event_id,
        "object": "event",
        "api_version": "2024-06-20",
        "created": int(time.time()),
        "livemode": False,
        "pending_webhooks": 0,
        "request": {"id": None, "idempotency_key": None},
        "type": event_type,
        "data": {
            "object": {
                "id": "sub_drill_1",
                "object": "subscription",
                "status": status,
                "customer": "cus_drill_1",
                "metadata": {"org_id": str(org_id)},
                "items": {
                    "object": "list",
                    "data": [
                        {
                            "id": "si_drill_1",
                            "object": "subscription_item",
                            "price": {"id": PRICE_PRO, "object": "price"},
                        }
                    ],
                },
            }
        },
    }


def run_webhooks(base_url: str, secret: str) -> None:
    org_id = seed_org()
    print(f"\nWebhook path against {base_url} (org {org_id})")

    status, body = post_event(base_url, subscription_event("evt_drill_1", "customer.subscription.updated", org_id, "active"), secret)
    check("a signed subscription event is accepted", status == 200, f"HTTP {status} {body[:120]}")
    check("an active subscription upgrades the org", org_state(org_id)["plan"] == "pro",
          f"plan is {org_state(org_id)['plan']!r}")

    # Stripe retries until it gets a 2xx and can duplicate deliveries.
    status, body = post_event(base_url, subscription_event("evt_drill_1", "customer.subscription.updated", org_id, "active"), secret)
    check("a replayed event is deduplicated", status == 200 and "duplicate" in body.lower(),
          f"HTTP {status} {body[:120]}")

    # The grace rule: a failed charge must not cost the customer their plan.
    post_event(base_url, subscription_event("evt_drill_2", "customer.subscription.updated", org_id, "past_due"), secret)
    check("past_due keeps the paid plan", org_state(org_id)["plan"] == "pro",
          f"plan is {org_state(org_id)['plan']!r}")

    before = org_state(org_id)["plan"]
    status, _ = post_event(base_url, subscription_event("evt_drill_3", "customer.subscription.deleted", org_id, "active"), secret, corrupt="signature")
    check("a wrongly-signed event is rejected", status == 400, f"HTTP {status}")
    check("a rejected event changes nothing", org_state(org_id)["plan"] == before)

    status, _ = post_event(base_url, subscription_event("evt_drill_4", "customer.subscription.updated", org_id, "canceled"), secret, corrupt="body")
    check("a tampered body is rejected", status == 400, f"HTTP {status}")
    check("a tampered event changes nothing", org_state(org_id)["plan"] == before)

    post_event(base_url, subscription_event("evt_drill_5", "customer.subscription.deleted", org_id, "active"), secret)
    check("a deleted subscription drops the org to free", org_state(org_id)["plan"] == "free",
          f"plan is {org_state(org_id)['plan']!r}")


# ── Outbound SDK calls ────────────────────────────────────────────────────────

def run_api() -> None:
    """Exercise the calls that talk to Stripe, against stripe-mock or test keys."""
    import stripe

    from app.core import billing
    from app.core.config import settings

    base = os.environ.get("STRIPE_API_BASE", "")
    if base:
        # Pointed at stripe-mock here rather than through a setting: production
        # has no business being able to redirect its payments API by env var.
        stripe.api_base = base
    print(f"\nOutbound API path against {base or 'live Stripe (test mode)'}")

    org_id = seed_org()
    try:
        customer_id = billing.ensure_customer(org_id, ORG_NAME, EMAIL)
        check("Customer.create is accepted", bool(customer_id), f"id {customer_id}")
    except Exception as exc:  # noqa: BLE001
        check("Customer.create is accepted", False, repr(exc))
        return

    try:
        url = billing.create_checkout_session(org_id, ORG_NAME, EMAIL, "pro")
        check("checkout.Session.create is accepted", bool(url), f"url {url[:60]}")
    except Exception as exc:  # noqa: BLE001
        check("checkout.Session.create is accepted", False, repr(exc))

    try:
        url = billing.create_portal_session(org_id)
        check("billing_portal.Session.create is accepted", bool(url), f"url {url[:60]}")
    except Exception as exc:  # noqa: BLE001
        check("billing_portal.Session.create is accepted", False, repr(exc))

    check("the configured price maps back to a plan",
          billing.plan_for_price(settings.STRIPE_PRICE_PRO) == "pro")


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "webhooks":
        run_webhooks(sys.argv[2], sys.argv[3])
    elif mode == "api":
        run_api()
    elif mode == "seed":
        print(seed_org())
        return
    else:
        print(f"usage: {sys.argv[0]} {{webhooks <base-url> <whsec>|api|seed}}", file=sys.stderr)
        raise SystemExit(2)
    finish()


if __name__ == "__main__":
    main()
