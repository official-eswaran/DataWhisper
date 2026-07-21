"""Per-tenant usage + plan endpoints (issue #6).

Owner/admin only, scoped to the caller's organization. Exposes the current
period's usage against plan limits (the data a billing UI or the admin console
consumes), plus an owner-only plan change for manual tier management ahead of
automated billing.
"""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator

from app.core.database import set_org_plan
from app.core.quota import PLAN_LIMITS, usage_summary
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


@router.put("/plan")
def change_plan(req: PlanRequest, user: Annotated[dict, Depends(get_current_user)]):
    """Change the org's plan. Owner-only (billing is an owner responsibility)."""
    if user.get("role") != "owner":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the org owner can change the plan")
    set_org_plan(user.get("org_id", -1), req.plan)
    return usage_summary(user.get("org_id", -1))
