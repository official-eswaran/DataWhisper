"""GDPR / data-subject rights.

* GET  /api/me/export — Article 15 (access): export all data about the caller.
* DELETE /api/me       — Article 17 (erasure): delete the caller's account + data.
* DELETE /api/org      — owner-initiated erasure of the whole organization.
"""
import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response

from app.core.database import (
    count_owners,
    delete_organization,
    delete_user_account,
    export_user_data,
    revoke_all_user_tokens,
)
from app.core.security import get_current_user

router = APIRouter()


@router.get("/me/export")
def export_my_data(current_user: Annotated[dict, Depends(get_current_user)]):
    data = export_user_data(current_user.get("org_id", -1), current_user.get("sub", ""))
    if data.get("profile") is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    body = json.dumps(data, indent=2)
    return Response(
        content=body,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="datawhisper_export_{current_user.get("sub")}.json"'},
    )


@router.delete("/me")
def delete_my_account(current_user: Annotated[dict, Depends(get_current_user)]):
    org_id = current_user.get("org_id", -1)
    username = current_user.get("sub", "")
    role = current_user.get("role", "")

    # A sole owner must delete the organization (or transfer ownership) rather
    # than orphan it.
    if role == "owner" and count_owners(org_id) <= 1:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "You are the only owner. Delete the organization or assign another owner first.",
        )

    datasets = delete_user_account(org_id, username)
    revoke_all_user_tokens(username)
    return {"detail": "Account deleted", "datasets_deleted": datasets}


@router.delete("/org")
def delete_my_organization(current_user: Annotated[dict, Depends(get_current_user)]):
    if current_user.get("role") != "owner":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only an organization owner can delete it")
    summary = delete_organization(current_user.get("org_id", -1))
    return {"detail": "Organization deleted", **summary}
