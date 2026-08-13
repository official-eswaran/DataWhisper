"""Stripe billing — hosted Checkout, Billing Portal, and webhook handling (issue #5).

Design notes, because the trust boundaries here matter:

* **Hosted only.** We create Checkout/Portal sessions and redirect; card details
  never reach this backend, which keeps PCI scope at SAQ-A. There is deliberately
  no card-handling code to review.
* **The webhook is the source of truth**, not the post-checkout redirect. A user
  can close the tab, replay, or hand-craft the success URL, so entitlements only
  ever change in response to a signature-verified Stripe event.
* **Opt-in.** With ``STRIPE_SECRET_KEY`` unset every entry point here is inert
  and the routes answer 503 — same convention as Sentry/OTel. Plans remain
  manually settable through ``PUT /api/usage/plan``.
* **Grace on failed payment.** ``past_due`` keeps the paid entitlement (the card
  may just need retrying); only ``canceled``/``unpaid`` drop the org to free.

Plan entitlements themselves live in ``app.core.quota.PLAN_LIMITS`` — this module
only decides *which* plan an org is on.
"""
from __future__ import annotations

import json
import logging

from app.core.config import settings
from app.core.database import (
    claim_stripe_event,
    find_org_by_customer,
    get_org_billing,
    set_org_billing,
)

logger = logging.getLogger(__name__)

FREE_PLAN = "free"

# Subscription statuses that still entitle the org to its paid plan. Stripe
# retries failed payments for a while before giving up; downgrading on the first
# failed charge would punish customers for an expiring card.
ENTITLED_STATUSES = frozenset({"active", "trialing", "past_due"})


def billing_enabled() -> bool:
    return settings.billing_enabled


def _client():
    """Return the configured Stripe module, or raise if billing is off.

    Imported lazily so the dependency stays optional at runtime and importing
    this module never costs anything when billing is disabled.
    """
    if not settings.billing_enabled:
        raise RuntimeError("Stripe billing is not configured (STRIPE_SECRET_KEY unset)")
    import stripe

    stripe.api_key = settings.STRIPE_SECRET_KEY
    return stripe


def plan_for_price(price_id: str) -> str | None:
    """Map a Stripe price id back to a plan name, or None if it's unrecognised."""
    for plan, price in settings.stripe_price_by_plan.items():
        if price == price_id:
            return plan
    return None


def price_for_plan(plan: str) -> str | None:
    return settings.stripe_price_by_plan.get(plan)


def _as_dict(obj) -> dict:
    """Plain nested dict for a Stripe SDK object, whatever the SDK version (#93).

    ``StripeObject`` was a ``dict`` subclass once and is not in v15, and the
    recursive-conversion helper has been renamed at least once. Everything in
    this module reads its inputs as plain dicts, so normalise here rather than
    guessing at an SDK API in each caller. ``json.loads(str(obj))`` is not used:
    it depends on the object's repr staying JSON, which is not a contract.
    """
    if isinstance(obj, dict):
        return dict(obj)
    for attr in ("to_dict_recursive", "_to_dict_recursive"):
        convert = getattr(obj, attr, None)
        if callable(convert):
            return convert()
    to_dict = getattr(obj, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    raise ValueError(f"cannot read a Stripe {type(obj).__name__} as a dict")


# ── Outbound: sessions we hand the user ───────────────────────────────────────

def ensure_customer(org_id: int, org_name: str, email: str) -> str:
    """Get or create the Stripe customer for an org, persisting the id."""
    billing = get_org_billing(org_id)
    if billing["customer_id"]:
        return billing["customer_id"]

    stripe = _client()
    customer = stripe.Customer.create(
        name=org_name,
        email=email,
        # metadata is what lets us recover the org from a Stripe-side object if
        # the local row is ever lost or restored from an older backup.
        metadata={"org_id": str(org_id)},
    )
    set_org_billing(org_id, customer_id=customer.id)
    return customer.id


def create_checkout_session(org_id: int, org_name: str, email: str, plan: str) -> str:
    """Create a hosted Checkout session for ``plan`` and return its URL."""
    price = price_for_plan(plan)
    if not price:
        raise ValueError(f"No Stripe price configured for plan '{plan}'")

    stripe = _client()
    customer_id = ensure_customer(org_id, org_name, email)
    session = stripe.checkout.Session.create(
        mode="subscription",
        customer=customer_id,
        line_items=[{"price": price, "quantity": 1}],
        success_url=settings.STRIPE_SUCCESS_URL or "https://example.com/billing/success",
        cancel_url=settings.STRIPE_CANCEL_URL or "https://example.com/billing/cancel",
        # Both are echoed back on the completed event; client_reference_id is the
        # one we assert on, so a session created for org A can never upgrade org B.
        client_reference_id=str(org_id),
        subscription_data={"metadata": {"org_id": str(org_id), "plan": plan}},
    )
    return session.url


def create_portal_session(org_id: int) -> str:
    """Create a Billing Portal session so the org can manage/cancel its plan."""
    billing = get_org_billing(org_id)
    if not billing["customer_id"]:
        raise ValueError("This organization has no Stripe customer yet")

    stripe = _client()
    session = stripe.billing_portal.Session.create(
        customer=billing["customer_id"],
        return_url=settings.STRIPE_SUCCESS_URL or "https://example.com/billing",
    )
    return session.url


def list_invoices(org_id: int, limit: int = 12) -> list[dict]:
    """The org's most recent invoices, trimmed to what a UI needs (#31).

    Returns ``[]`` for an org that has never checked out — it has no Stripe
    customer, so there is nothing to list and that is not an error.

    Deliberately a projection rather than a passthrough. A Stripe invoice
    carries a hundred-odd fields including internal ids, the full customer
    object and line-item metadata; forwarding all of it to the browser would
    leak more than the page shows and would tie the frontend to Stripe's schema.

    Amounts stay in the currency's minor unit (cents), as Stripe reports them.
    Dividing by 100 here would be wrong for zero-decimal currencies like JPY,
    and the formatting belongs where the locale is known anyway.
    """
    billing_row = get_org_billing(org_id)
    customer_id = billing_row["customer_id"]
    if not customer_id:
        return []

    stripe = _client()
    response = stripe.Invoice.list(customer=customer_id, limit=max(1, min(limit, 100)))
    data = _as_dict(response).get("data") or []
    return [_invoice_summary(_as_dict(invoice)) for invoice in data]


def _invoice_summary(invoice: dict) -> dict:
    """One invoice as the billing page needs it, and nothing else."""
    return {
        "id": invoice.get("id") or "",
        # Stripe's human-facing reference (DW-0001). Drafts have none yet.
        "number": invoice.get("number") or "",
        # draft|open|paid|uncollectible|void
        "status": invoice.get("status") or "",
        "amount_due": int(invoice.get("amount_due") or 0),
        "amount_paid": int(invoice.get("amount_paid") or 0),
        "currency": (invoice.get("currency") or "").lower(),
        "created": int(invoice.get("created") or 0),
        # Stripe-hosted pages. Both can be absent on a draft, and the UI has to
        # cope rather than render a dead link.
        "hosted_invoice_url": invoice.get("hosted_invoice_url") or "",
        "invoice_pdf": invoice.get("invoice_pdf") or "",
    }


# ── Inbound: webhook ──────────────────────────────────────────────────────────

def verify_event(payload: bytes, signature: str) -> dict:
    """Verify a webhook payload's signature and return the parsed event.

    Raises ValueError on a bad signature or malformed body. Signature
    verification is the *only* thing authenticating this endpoint, so an
    unset webhook secret is treated as a hard failure rather than a skip.

    **The dict comes from the raw payload, not from the SDK object** (#93).
    ``construct_event`` authenticates the bytes; once it has, those bytes are
    the event, and parsing them gives a plain nested dict that every consumer
    here already expects. Converting the returned ``stripe.Event`` instead is
    what broke: it was a ``dict`` subclass in older SDKs and is not in v15, so
    the conversion branch ran, called a method that no longer exists, and every
    verified webhook 500'd. Reading the payload has no version surface at all.
    """
    if not settings.STRIPE_WEBHOOK_SECRET:
        raise ValueError("STRIPE_WEBHOOK_SECRET is not configured")

    stripe = _client()
    try:
        stripe.Webhook.construct_event(payload, signature, settings.STRIPE_WEBHOOK_SECRET)
    except Exception as exc:  # SignatureVerificationError or JSON decode failure
        raise ValueError(f"Invalid Stripe signature: {exc}") from exc
    return json.loads(payload)


def _plan_from_subscription(subscription: dict) -> str | None:
    """Work out the entitled plan from a subscription object.

    Prefers the price id (authoritative — it's what the customer is being
    charged for) and falls back to the metadata we set at checkout.
    """
    items = (subscription.get("items") or {}).get("data") or []
    for item in items:
        price_id = (item.get("price") or {}).get("id", "")
        plan = plan_for_price(price_id)
        if plan:
            return plan
    meta_plan = (subscription.get("metadata") or {}).get("plan")
    return meta_plan if meta_plan in settings.stripe_price_by_plan else None


def _org_for_subscription(subscription: dict) -> int | None:
    meta_org = (subscription.get("metadata") or {}).get("org_id")
    if meta_org and str(meta_org).isdigit():
        return int(meta_org)
    return find_org_by_customer(subscription.get("customer") or "")


def _apply_subscription(subscription: dict) -> str:
    """Sync one subscription object onto the local org row."""
    org_id = _org_for_subscription(subscription)
    if org_id is None:
        logger.warning(
            "stripe: subscription %s has no resolvable org", subscription.get("id")
        )
        return "ignored: unknown org"

    status = subscription.get("status") or "canceled"
    plan = _plan_from_subscription(subscription)

    if status in ENTITLED_STATUSES and plan:
        set_org_billing(
            org_id,
            plan=plan,
            subscription_id=subscription.get("id") or "",
            status=status,
        )
        return f"org {org_id} -> {plan} ({status})"

    # Cancelled, unpaid, or a price we don't recognise: fall back to free rather
    # than leaving an org entitled to something nobody is paying for.
    set_org_billing(org_id, plan=FREE_PLAN, subscription_id="", status=status)
    return f"org {org_id} -> free ({status})"


def handle_event(event: dict) -> str:
    """Apply a verified Stripe event. Returns a short human-readable outcome.

    Safe to call twice with the same event — the id is claimed first, and the
    underlying writes are idempotent anyway.
    """
    event_id = event.get("id") or ""
    event_type = event.get("type") or ""

    if not claim_stripe_event(event_id, event_type):
        return f"duplicate {event_id}, skipped"

    obj = ((event.get("data") or {}).get("object")) or {}

    if event_type in (
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
    ):
        # A deletion event's status is not always "canceled" in the payload, so
        # force the downgrade path rather than trusting the field.
        if event_type == "customer.subscription.deleted":
            obj = {**obj, "status": "canceled"}
        return _apply_subscription(obj)

    if event_type == "checkout.session.completed":
        # The session tells us which org paid; the subscription tells us what
        # they bought. We re-fetch rather than trust the session's expansion.
        org_ref = obj.get("client_reference_id")
        customer_id = obj.get("customer") or ""
        subscription_id = obj.get("subscription") or ""
        if org_ref and str(org_ref).isdigit() and customer_id:
            set_org_billing(int(org_ref), customer_id=customer_id)
        if not subscription_id:
            return "checkout completed, no subscription"
        stripe = _client()
        subscription = stripe.Subscription.retrieve(subscription_id)
        return _apply_subscription(_as_dict(subscription))

    return f"ignored event type {event_type}"
