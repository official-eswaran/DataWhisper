"""Audit-log endpoint — admin only, paginated, scoped to the caller's org."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.database import fetch_audit_logs, verify_audit_chain
from app.core.security import require_admin

router = APIRouter()


@router.get("/logs")
def get_audit_logs(
    admin: Annotated[dict, Depends(require_admin)],
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    items, total = fetch_audit_logs(org_id=admin.get("org_id", -1), limit=limit, offset=offset)
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/verify")
def verify_audit_integrity(admin: Annotated[dict, Depends(require_admin)]):
    """Recompute the organization's audit hash chain and report tampering."""
    return verify_audit_chain(org_id=admin.get("org_id", -1))
