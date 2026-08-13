"""Stripe billing: entitlement mapping, webhook trust, and route guards (issue #5).

No test talks to Stripe. Outbound calls are monkeypatched and inbound webhooks
are fed straight to the handler with signature verification stubbed, so the
suite stays hermetic and runs with billing unconfigured by default.
"""
import uuid

import pytest

from app.core import billing
from app.core.config import settings
from app.core.database import (
    claim_stripe_event,
    create_organization_with_owner,
    find_org_by_customer,
    get_org_billing,
    set_org_billing,
)


@pytest.fixture(autouse=True)
def _init_db(client):
    """Depend on the app client so init_db() has built the schema."""


@pytest.fixture
def prices(monkeypatch):
    """Configure plan→price mapping without enabling live Stripe calls."""
    monkeypatch.setattr(settings, "STRIPE_PRICE_PRO", "price_pro_123")
    monkeypatch.setattr(settings, "STRIPE_PRICE_ENTERPRISE", "price_ent_456")
    return {"pro": "price_pro_123", "enterprise": "price_ent_456"}


def _new_org() -> int:
    suffix = uuid.uuid4().hex[:8]
    org = create_organization_with_owner(
        f"billing-{suffix}", f"user-{suffix}", f"user-{suffix}@x.io", "hash"
    )
    return org["org_id"]


def _sub(org_id, price="price_pro_123", status="active", sub_id="sub_1", customer="cus_1"):
    return {
        "id": sub_id,
        "customer": customer,
        "status": status,
        "items": {"data": [{"price": {"id": price}}]},
        "metadata": {"org_id": str(org_id)},
    }


def _event(event_type, obj, event_id=None):
    return {
        "id": event_id or f"evt_{uuid.uuid4().hex[:12]}",
        "type": event_type,
        "data": {"object": obj},
    }


# ── Disabled by default ───────────────────────────────────────────────────────

def test_billing_disabled_without_secret_key():
    assert settings.billing_enabled is False
    assert billing.billing_enabled() is False


def test_client_raises_when_unconfigured():
    with pytest.raises(RuntimeError, match="not configured"):
        billing._client()


def test_checkout_route_returns_503_when_unconfigured(client, admin_token):
    r = client.post(
        "/api/billing/checkout",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"plan": "pro"},
    )
    assert r.status_code == 503


# ── Plan ↔ price mapping ──────────────────────────────────────────────────────

def test_price_plan_roundtrip(prices):
    assert billing.price_for_plan("pro") == "price_pro_123"
    assert billing.plan_for_price("price_ent_456") == "enterprise"


def test_unknown_price_maps_to_no_plan(prices):
    assert billing.plan_for_price("price_someone_elses") is None


def test_unconfigured_plan_has_no_price():
    assert billing.price_for_plan("pro") is None


# ── Webhook: subscription lifecycle ───────────────────────────────────────────

def test_active_subscription_upgrades_org(prices):
    org_id = _new_org()
    result = billing.handle_event(
        _event("customer.subscription.updated", _sub(org_id))
    )
    assert "pro" in result
    info = get_org_billing(org_id)
    assert info["plan"] == "pro"
    assert info["status"] == "active"
    assert info["subscription_id"] == "sub_1"


def test_enterprise_price_grants_enterprise(prices):
    org_id = _new_org()
    billing.handle_event(
        _event("customer.subscription.updated", _sub(org_id, price="price_ent_456"))
    )
    assert get_org_billing(org_id)["plan"] == "enterprise"


def test_past_due_keeps_paid_plan(prices):
    """A failed charge must not instantly strip entitlements — Stripe retries."""
    org_id = _new_org()
    billing.handle_event(_event("customer.subscription.updated", _sub(org_id)))
    billing.handle_event(
        _event("customer.subscription.updated", _sub(org_id, status="past_due"))
    )
    info = get_org_billing(org_id)
    assert info["plan"] == "pro"
    assert info["status"] == "past_due"


def test_canceled_subscription_downgrades_to_free(prices):
    org_id = _new_org()
    billing.handle_event(_event("customer.subscription.updated", _sub(org_id)))
    billing.handle_event(
        _event("customer.subscription.deleted", _sub(org_id, status="active"))
    )
    info = get_org_billing(org_id)
    assert info["plan"] == "free"
    assert info["subscription_id"] == ""


def test_unpaid_subscription_downgrades(prices):
    org_id = _new_org()
    billing.handle_event(_event("customer.subscription.updated", _sub(org_id)))
    billing.handle_event(
        _event("customer.subscription.updated", _sub(org_id, status="unpaid"))
    )
    assert get_org_billing(org_id)["plan"] == "free"


def test_unrecognised_price_does_not_grant_a_plan(prices):
    """An org must never end up entitled to a price we don't know about."""
    org_id = _new_org()
    billing.handle_event(
        _event("customer.subscription.updated", _sub(org_id, price="price_bogus"))
    )
    assert get_org_billing(org_id)["plan"] == "free"


def test_subscription_for_unknown_org_is_ignored(prices):
    event = _event("customer.subscription.updated", {
        "id": "sub_x", "customer": "cus_nobody", "status": "active",
        "items": {"data": [{"price": {"id": "price_pro_123"}}]}, "metadata": {},
    })
    assert "unknown org" in billing.handle_event(event)


def test_org_resolved_by_customer_id_when_metadata_missing(prices):
    """Subscriptions created outside checkout carry no metadata — fall back."""
    org_id = _new_org()
    set_org_billing(org_id, customer_id="cus_lookup_1")
    sub = _sub(org_id, customer="cus_lookup_1")
    sub["metadata"] = {}
    billing.handle_event(_event("customer.subscription.updated", sub))
    assert get_org_billing(org_id)["plan"] == "pro"


# ── Webhook: idempotency ──────────────────────────────────────────────────────

def test_duplicate_event_is_skipped(prices):
    org_id = _new_org()
    event = _event("customer.subscription.updated", _sub(org_id))
    first = billing.handle_event(event)
    second = billing.handle_event(event)
    assert "pro" in first
    assert "duplicate" in second


def test_claim_event_is_single_use():
    event_id = f"evt_{uuid.uuid4().hex[:12]}"
    assert claim_stripe_event(event_id, "test") is True
    assert claim_stripe_event(event_id, "test") is False


def test_unknown_event_type_is_ignored_not_errored(prices):
    result = billing.handle_event(_event("invoice.created", {"id": "in_1"}))
    assert "ignored" in result


# ── Webhook: signature verification ───────────────────────────────────────────

def test_verify_event_rejects_when_no_webhook_secret(monkeypatch):
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", "")
    with pytest.raises(ValueError, match="STRIPE_WEBHOOK_SECRET"):
        billing.verify_event(b"{}", "sig")


def test_verify_event_rejects_bad_signature(monkeypatch):
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", "whsec_test")
    with pytest.raises(ValueError, match="Invalid Stripe signature"):
        billing.verify_event(b'{"id":"evt_1"}', "t=1,v1=deadbeef")


def test_webhook_route_rejects_unsigned_payload(client, monkeypatch):
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", "whsec_test")
    r = client.post("/api/billing/webhook", json={"id": "evt_forged", "type": "x"})
    assert r.status_code == 400


# ── Route authorization ───────────────────────────────────────────────────────

def test_checkout_requires_owner_role(client, manager_token, monkeypatch):
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_x")
    r = client.post(
        "/api/billing/checkout",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"plan": "pro"},
    )
    assert r.status_code == 403


def test_checkout_rejects_free_plan(client, admin_token, monkeypatch):
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_x")
    r = client.post(
        "/api/billing/checkout",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"plan": "free"},
    )
    assert r.status_code == 422


def test_billing_status_requires_auth(client):
    assert client.get("/api/billing/").status_code == 401


def test_billing_status_reports_plan(client, admin_token):
    r = client.get("/api/billing/", headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is False
    assert body["plan"] == "free"
    assert "usage" in body


# ── Customer creation ─────────────────────────────────────────────────────────

def test_ensure_customer_is_cached_after_first_create(prices, monkeypatch):
    org_id = _new_org()
    calls = []

    class _Customer:
        @staticmethod
        def create(**kwargs):
            calls.append(kwargs)
            return type("C", (), {"id": "cus_created_1"})()

    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setattr(billing, "_client", lambda: type("S", (), {"Customer": _Customer}))

    assert billing.ensure_customer(org_id, "Acme", "a@acme.io") == "cus_created_1"
    assert billing.ensure_customer(org_id, "Acme", "a@acme.io") == "cus_created_1"
    assert len(calls) == 1, "second call must reuse the stored customer id"
    assert find_org_by_customer("cus_created_1") == org_id


def test_checkout_session_rejects_plan_without_price(monkeypatch):
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setattr(settings, "STRIPE_PRICE_PRO", "")
    with pytest.raises(ValueError, match="No Stripe price"):
        billing.create_checkout_session(1, "Acme", "a@acme.io", "pro")


def test_portal_requires_existing_customer(monkeypatch):
    org_id = _new_org()
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_x")
    with pytest.raises(ValueError, match="no Stripe customer"):
        billing.create_portal_session(org_id)


# ── Route happy paths (issue #28: this is the payment code, it needs cover) ────

def _enable(monkeypatch):
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", "whsec_test")


def test_checkout_route_returns_the_redirect_url(client, admin_token, monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(
        billing, "create_checkout_session",
        lambda **kw: "https://checkout.stripe.com/c/session_123",
    )
    r = client.post(
        "/api/billing/checkout",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"plan": "pro"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["checkout_url"].startswith("https://checkout.stripe.com/")


def test_checkout_route_surfaces_configuration_errors_as_400(client, admin_token, monkeypatch):
    """A missing price id is the operator's mistake, not a server fault."""
    _enable(monkeypatch)

    def boom(**kw):
        raise ValueError("No Stripe price configured for plan 'pro'")

    monkeypatch.setattr(billing, "create_checkout_session", boom)
    r = client.post(
        "/api/billing/checkout",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"plan": "pro"},
    )
    assert r.status_code == 400
    assert "No Stripe price" in r.json()["detail"]


def test_portal_route_returns_the_portal_url(client, admin_token, monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(
        billing, "create_portal_session", lambda org_id: "https://billing.stripe.com/p/session_1"
    )
    r = client.post("/api/billing/portal", headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 200, r.text
    assert r.json()["portal_url"].startswith("https://billing.stripe.com/")


def test_portal_route_requires_a_customer(client, admin_token, monkeypatch):
    _enable(monkeypatch)

    def boom(org_id):
        raise ValueError("Organization has no Stripe customer")

    monkeypatch.setattr(billing, "create_portal_session", boom)
    r = client.post("/api/billing/portal", headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 400


def test_portal_route_requires_owner(client, manager_token, monkeypatch):
    _enable(monkeypatch)
    r = client.post("/api/billing/portal", headers={"Authorization": f"Bearer {manager_token}"})
    assert r.status_code == 403


def test_portal_route_503_when_billing_is_off(client, admin_token):
    r = client.post("/api/billing/portal", headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 503


def test_webhook_route_processes_a_verified_event(client, monkeypatch):
    """The only path that may change entitlements — signature verified, then handled."""
    _enable(monkeypatch)
    monkeypatch.setattr(billing, "verify_event", lambda payload, sig: {"type": "ping", "id": "e1"})
    monkeypatch.setattr(billing, "handle_event", lambda event: "ignored")

    r = client.post(
        "/api/billing/webhook",
        content=b"{}",
        headers={"stripe-signature": "t=1,v1=deadbeef"},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"received": True, "result": "ignored"}


def test_webhook_route_503_when_billing_is_off(client):
    r = client.post("/api/billing/webhook", content=b"{}")
    assert r.status_code == 503


# ── The unstubbed path (#93) ──────────────────────────────────────────────────
#
# Everything above enters after signature verification: `verify_event` is either
# monkeypatched or exercised only for its rejection cases. That left the success
# path — construct a real signature, verify it, hand the result to
# `handle_event` — unexecuted by anything, and it was broken from the day
# billing shipped. `stripe.Event` stopped being a `dict` subclass, so
# `verify_event` took its conversion branch, called a method the SDK no longer
# has, and every genuine webhook 500'd. No subscription event could ever apply.
#
# These tests use a real HMAC and do not patch `verify_event`, so the branch
# that broke is the branch under test.


def _stripe_signature(payload: bytes, secret: str) -> str:
    """A Stripe-Signature header, built the way Stripe builds one.

    Deliberately hand-rolled rather than taken from the SDK: a signature made by
    the same code that verifies it would agree with itself no matter what either
    side did.
    """
    import hashlib
    import hmac
    import time

    ts = int(time.time())
    digest = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={digest}"


def _stripe_shaped_event(org_id: int, event_type: str, status: str, event_id: str) -> dict:
    """A payload shaped like one Stripe actually sends.

    The `object` discriminators and envelope fields are load-bearing: the SDK
    needs them to build a typed Event, and `construct_event` raises without
    them. The `_event`/`_sub` helpers above omit them, which is fine when the
    handler is called directly and fatal when it is not.
    """
    import time

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
                "id": "sub_real_1",
                "object": "subscription",
                "status": status,
                "customer": "cus_real_1",
                "metadata": {"org_id": str(org_id)},
                "items": {
                    "object": "list",
                    "data": [
                        {
                            "id": "si_real_1",
                            "object": "subscription_item",
                            "price": {"id": "price_pro_123", "object": "price"},
                        }
                    ],
                },
            }
        },
    }


def _post_signed(client, event: dict, secret: str = "whsec_test_93") -> object:
    import json as _json

    payload = _json.dumps(event).encode()
    return client.post(
        "/api/billing/webhook",
        content=payload,
        headers={"stripe-signature": _stripe_signature(payload, secret)},
    )


@pytest.fixture
def signed_webhooks(monkeypatch, prices):
    _enable(monkeypatch)
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", "whsec_test_93")


def test_a_genuinely_signed_event_is_accepted(client, signed_webhooks):
    """The regression test for #93: this returned 500 before the fix."""
    org_id = _new_org()
    r = _post_signed(
        client,
        _stripe_shaped_event(org_id, "customer.subscription.updated", "active", "evt_real_1"),
    )
    assert r.status_code == 200, r.text
    assert get_org_billing(org_id)["plan"] == "pro"


def test_verify_event_returns_a_plain_dict(monkeypatch, prices):
    """`handle_event` indexes into nested structures, so a shallow conversion —
    the other tempting fix — would fail at `items.data[0].price.id`."""
    _enable(monkeypatch)
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", "whsec_test_93")
    import json as _json

    event = _stripe_shaped_event(1, "customer.subscription.updated", "active", "evt_real_2")
    payload = _json.dumps(event).encode()

    parsed = billing.verify_event(payload, _stripe_signature(payload, "whsec_test_93"))

    assert type(parsed) is dict
    nested = parsed["data"]["object"]["items"]["data"][0]["price"]["id"]
    assert nested == "price_pro_123"
    assert type(parsed["data"]["object"]) is dict


def test_a_signed_cancellation_downgrades_the_org(client, signed_webhooks):
    org_id = _new_org()
    _post_signed(
        client,
        _stripe_shaped_event(org_id, "customer.subscription.updated", "active", "evt_real_3"),
    )
    assert get_org_billing(org_id)["plan"] == "pro"

    r = _post_signed(
        client,
        _stripe_shaped_event(org_id, "customer.subscription.deleted", "active", "evt_real_4"),
    )
    assert r.status_code == 200, r.text
    assert get_org_billing(org_id)["plan"] == "free"


def test_a_replayed_signed_event_is_deduplicated(client, signed_webhooks):
    org_id = _new_org()
    event = _stripe_shaped_event(org_id, "customer.subscription.updated", "active", "evt_real_5")
    assert _post_signed(client, event).status_code == 200
    second = _post_signed(client, event)
    assert second.status_code == 200
    assert "duplicate" in second.json()["result"]


def test_a_tampered_body_is_rejected_after_signing(client, signed_webhooks):
    """Signature over the original bytes, then the bytes change — the exact
    thing the raw-body handling in the route exists to catch."""
    import json as _json

    org_id = _new_org()
    event = _stripe_shaped_event(org_id, "customer.subscription.updated", "active", "evt_real_6")
    payload = _json.dumps(event).encode()
    header = _stripe_signature(payload, "whsec_test_93")

    r = client.post(
        "/api/billing/webhook",
        content=payload.replace(b'"active"', b'"acTive"'),
        headers={"stripe-signature": header},
    )
    assert r.status_code == 400
    assert get_org_billing(org_id)["plan"] == "free"


# ── Invoice history (#31) ─────────────────────────────────────────────────────
#
# Read-only, and deliberately a projection rather than a passthrough: a Stripe
# invoice carries a hundred-odd fields including the full customer object, and
# forwarding them would leak more than the page shows while tying the frontend
# to Stripe's schema.


def _stripe_invoice(**overrides):
    invoice = {
        "id": "in_1",
        "object": "invoice",
        "number": "DW-0001",
        "status": "paid",
        "amount_due": 2900,
        "amount_paid": 2900,
        "currency": "USD",
        "created": 1_760_000_000,
        "hosted_invoice_url": "https://invoice.stripe.com/i/1",
        "invoice_pdf": "https://invoice.stripe.com/i/1.pdf",
        # Fields the projection must drop rather than forward.
        "customer_email": "owner@example.com",
        "customer": {"id": "cus_1", "name": "Acme"},
        "lines": {"data": [{"description": "Pro plan"}]},
    }
    invoice.update(overrides)
    return invoice


def _stub_invoice_list(monkeypatch, invoices, capture=None):
    class _Invoice:
        @staticmethod
        def list(**kwargs):
            if capture is not None:
                capture.update(kwargs)
            return {"object": "list", "data": invoices}

    monkeypatch.setattr(billing, "_client", lambda: type("S", (), {"Invoice": _Invoice}))


def test_an_org_without_a_customer_has_no_invoices(monkeypatch, prices):
    """A free org that never checked out. Not an error — the normal state."""
    _enable(monkeypatch)
    org_id = _new_org()

    def explode():
        raise AssertionError("Stripe must not be called for an org with no customer")

    monkeypatch.setattr(billing, "_client", explode)
    assert billing.list_invoices(org_id) == []


def test_invoices_are_projected_not_forwarded(monkeypatch, prices):
    _enable(monkeypatch)
    org_id = _new_org()
    set_org_billing(org_id, customer_id="cus_1")
    _stub_invoice_list(monkeypatch, [_stripe_invoice()])

    (invoice,) = billing.list_invoices(org_id)

    assert invoice == {
        "id": "in_1",
        "number": "DW-0001",
        "status": "paid",
        "amount_due": 2900,
        "amount_paid": 2900,
        "currency": "usd",
        "created": 1_760_000_000,
        "hosted_invoice_url": "https://invoice.stripe.com/i/1",
        "invoice_pdf": "https://invoice.stripe.com/i/1.pdf",
    }
    # The exact assertion above is the point: anything Stripe adds later has to
    # be let through deliberately rather than arriving by default.
    assert "customer_email" not in invoice
    assert "lines" not in invoice


def test_amounts_stay_in_minor_units(monkeypatch, prices):
    """Dividing by 100 here would be wrong for zero-decimal currencies, and the
    formatting belongs where the locale is known."""
    _enable(monkeypatch)
    org_id = _new_org()
    set_org_billing(org_id, customer_id="cus_1")
    _stub_invoice_list(monkeypatch, [_stripe_invoice(amount_paid=2900, currency="jpy")])

    (invoice,) = billing.list_invoices(org_id)
    assert invoice["amount_paid"] == 2900
    assert invoice["currency"] == "jpy"


def test_a_draft_invoice_survives_its_missing_fields(monkeypatch, prices):
    """Drafts have no number and no hosted pages. Rendering a dead link is worse
    than rendering none, so the projection must not invent them."""
    _enable(monkeypatch)
    org_id = _new_org()
    set_org_billing(org_id, customer_id="cus_1")
    _stub_invoice_list(
        monkeypatch,
        [_stripe_invoice(status="draft", number=None, hosted_invoice_url=None, invoice_pdf=None)],
    )

    (invoice,) = billing.list_invoices(org_id)
    assert invoice["number"] == ""
    assert invoice["hosted_invoice_url"] == ""
    assert invoice["invoice_pdf"] == ""


def test_only_the_orgs_own_invoices_are_requested(monkeypatch, prices):
    """The customer id comes from the org row, so one org cannot list another's."""
    _enable(monkeypatch)
    org_id = _new_org()
    set_org_billing(org_id, customer_id="cus_mine")
    captured = {}
    _stub_invoice_list(monkeypatch, [], capture=captured)

    billing.list_invoices(org_id)
    assert captured["customer"] == "cus_mine"


def test_the_page_size_is_bounded(monkeypatch, prices):
    """Stripe caps `limit` at 100 and errors above it; clamp rather than pass
    through whatever a caller asks for."""
    _enable(monkeypatch)
    org_id = _new_org()
    set_org_billing(org_id, customer_id="cus_1")
    captured = {}
    _stub_invoice_list(monkeypatch, [], capture=captured)

    billing.list_invoices(org_id, limit=5000)
    assert captured["limit"] == 100
    billing.list_invoices(org_id, limit=0)
    assert captured["limit"] == 1


def test_invoice_route_is_owner_only(client, manager_token, monkeypatch):
    _enable(monkeypatch)
    r = client.get(
        "/api/billing/invoices", headers={"Authorization": f"Bearer {manager_token}"}
    )
    assert r.status_code == 403


def test_invoice_route_503_when_billing_is_off(client, admin_token):
    r = client.get(
        "/api/billing/invoices", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert r.status_code == 503


def test_invoice_route_returns_the_list(client, admin_token, monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(billing, "list_invoices", lambda org_id: [{"id": "in_1"}])
    r = client.get(
        "/api/billing/invoices", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert r.status_code == 200
    assert r.json() == {"invoices": [{"id": "in_1"}]}


def test_a_stripe_outage_is_a_502_not_a_500(client, admin_token, monkeypatch):
    """Stripe being unreachable is not this application failing, and the message
    has to tell the user to retry rather than reading as data loss."""
    _enable(monkeypatch)

    def unreachable(org_id):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(billing, "list_invoices", unreachable)
    r = client.get(
        "/api/billing/invoices", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert r.status_code == 502
    assert "try again" in r.json()["detail"].lower()
