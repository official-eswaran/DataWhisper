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
