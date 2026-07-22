"""Per-tenant usage + plan endpoints (issue #6).

Owner/admin only, scoped to the caller's organization. Exposes the current
period's usage against plan limits (the data a billing UI or the admin console
consumes), plus an owner-only plan change for manual tier management.

That manual change exists only for deployments **without** Stripe. Once billing
is configured, Stripe is the single source of truth for a plan: the webhook
writes ``organizations.plan`` on every subscription change, so a hand-set plan
would be a free upgrade that the next webhook silently overwrites. The endpoint
therefore refuses when ``billing_enabled`` — see issue #17.
"""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator

from app.core.config import settings
from app.core.database import set_org_plan
from app.core.quota import PLAN_LIMITS, limits_report, usage_summary
from app.core.security import get_current_user, require_admin

router = APIRouter()


class PlanRequest(BaseModel):
    plan: str

    @field_validator("plan")
    @classmethod
    def check_plan(cls, v: str) -> str:
        if v not in PLAN_LIMITS:
            raise ValueError(f"plan must be one of {sorted(PLAN_LIMITS)}")
        return v


@router.get("/")
def get_usage(admin: Annotated[dict, Depends(require_admin)]):
    """Current-period usage vs plan limits for the caller's org."""
    return usage_summary(admin.get("org_id", -1))


@router.get("/limits")
def get_limits(admin: Annotated[dict, Depends(require_admin)]):
    """Row-ceiling calibration: measured bytes-per-row vs the configured caps.

    The ceilings were originally picked without usage data (issue #24). This
    reports what real uploads actually weigh, whether the sample is yet big
    enough to trust, and how many maximum-size uploads each plan's ceiling
    really buys — the evidence needed to set the caps from data.
    """
    return limits_report()


@router.put("/plan")
def change_plan(req: PlanRequest, user: Annotated[dict, Depends(get_current_user)]):
    """Manually set the org's plan. Owner-only, and only when billing is off.

    With Stripe configured, plans are changed through checkout (upgrade) and the
    billing portal (downgrade/cancel); a manual override here would desync from
    the subscription and be reverted by the next webhook. So we refuse rather
    than accept a change that won't stick — see issue #17.
    """
    if user.get("role") != "owner":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the org owner can change the plan")
    if settings.billing_enabled:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Plans are managed through Stripe on this deployment. Use checkout to "
            "upgrade or the billing portal to change or cancel your subscription.",
        )
    set_org_plan(user.get("org_id", -1), req.plan)
    return usage_summary(user.get("org_id", -1))
