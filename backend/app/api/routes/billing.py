"""Stripe billing endpoints (issue #5).

Three routes with deliberately different trust models:

* ``POST /checkout`` and ``POST /portal`` are owner-only and act on the caller's
  own org — the org id comes from the JWT, never from the request body, so one
  org cannot start a checkout that upgrades another.
* ``POST /webhook`` is unauthenticated by necessity (Stripe calls it) and is
  instead authenticated by HMAC signature over the raw request body. It must
  read the body as bytes: re-serialising parsed JSON changes the bytes and the
  signature will not match.

See ``app.core.billing`` for the entitlement rules.
"""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, field_validator

from app.core import billing
from app.core.database import get_org_billing, get_org_name, get_user_by_username
from app.core.quota import PLAN_LIMITS, usage_summary
from app.core.security import get_current_user

router = APIRouter()


class CheckoutRequest(BaseModel):
    plan: str

    @field_validator("plan")
    @classmethod
    def check_plan(cls, v: str) -> str:
        # "free" is excluded on purpose — downgrading is a portal/cancel action,
        # not something you check out for.
        if v not in PLAN_LIMITS or v == "free":
            raise ValueError("plan must be a paid plan")
        return v


def _require_owner(user: dict) -> dict:
    if user.get("role") != "owner":
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Only the org owner can manage billing"
        )
    return user


def _require_billing_enabled() -> None:
    if not billing.billing_enabled():
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Billing is not configured on this deployment",
        )


@router.get("/")
def billing_status(user: Annotated[dict, Depends(get_current_user)]):
    """Plan, subscription status, and current usage for the caller's org."""
    org_id = user.get("org_id", -1)
    info = get_org_billing(org_id)
    return {
        "enabled": billing.billing_enabled(),
        "plan": info["plan"],
        "status": info["status"],
        "has_subscription": bool(info["subscription_id"]),
        "available_plans": sorted(PLAN_LIMITS),
        "usage": usage_summary(org_id),
    }


@router.post("/checkout")
def start_checkout(
    req: CheckoutRequest, user: Annotated[dict, Depends(get_current_user)]
):
    """Create a hosted Stripe Checkout session; returns the URL to redirect to."""
    _require_billing_enabled()
    _require_owner(user)

    org_id = user.get("org_id", -1)
    account = get_user_by_username(user.get("sub", "")) or {}
    try:
        url = billing.create_checkout_session(
            org_id=org_id,
            org_name=get_org_name(org_id),
            email=account.get("email", ""),
            plan=req.plan,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return {"checkout_url": url}


@router.post("/portal")
def open_portal(user: Annotated[dict, Depends(get_current_user)]):
    """Create a Stripe Billing Portal session for managing or cancelling a plan."""
    _require_billing_enabled()
    _require_owner(user)

    try:
        url = billing.create_portal_session(user.get("org_id", -1))
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return {"portal_url": url}


@router.post("/webhook")
async def stripe_webhook(request: Request):
    """Receive Stripe subscription events. Authenticated by signature only.

    Returns 400 on an unverifiable payload so Stripe surfaces the failure in the
    dashboard, and 200 on anything we successfully processed *or* deliberately
    ignored — a non-2xx makes Stripe retry, which we only want for real faults.
    """
    _require_billing_enabled()

    payload = await request.body()
    signature = request.headers.get("stripe-signature", "")
    try:
        event = billing.verify_event(payload, signature)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    result = billing.handle_event(event)
    return {"received": True, "result": result}
