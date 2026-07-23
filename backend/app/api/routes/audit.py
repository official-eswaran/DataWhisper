"""Audit-log endpoint — admin only, paginated, scoped to the caller's org."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.database import (
    fetch_audit_logs,
    latest_audit_checkpoint,
    verify_audit_chain,
    write_audit_checkpoint,
)
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
def verify_audit_integrity(
    admin: Annotated[dict, Depends(require_admin)],
    full: bool = Query(
        True,
        description=(
            "True (default) re-walks the whole chain — the answer an audit "
            "needs, cost grows with history. False verifies only entries after "
            "the newest signed checkpoint: bounded, but blind to tampering "
            "older than that checkpoint."
        ),
    ),
    since_id: int | None = Query(
        None,
        ge=0,
        description=(
            "With full=false, anchor on the newest checkpoint at or before this "
            "audit-log id instead of the newest one overall."
        ),
    ),
):
    """Recompute the organization's audit hash chain and report tampering.

    The response always states its own ``scope`` and ``verified_from_id``, so an
    incremental pass can't be read as a clean bill of health for all of history
    (issue #30).
    """
    return verify_audit_chain(org_id=admin.get("org_id", -1), full=full, since_id=since_id)


@router.get("/checkpoint")
def get_latest_checkpoint(admin: Annotated[dict, Depends(require_admin)]):
    """The newest signed checkpoint, or null if the chain has none yet."""
    return {"checkpoint": latest_audit_checkpoint(admin.get("org_id", -1))}


@router.post("/checkpoint")
def create_checkpoint(admin: Annotated[dict, Depends(require_admin)]):
    """Sign the current chain head now, rather than waiting for the interval.

    Verifies everything since the previous checkpoint first and refuses if that
    fails — a checkpoint over corrupt history would launder the corruption into
    every later bounded verify.
    """
    return write_audit_checkpoint(admin.get("org_id", -1))
