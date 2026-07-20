"""Organization user management — owner/admin only, scoped to their org."""
import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, field_validator

from app.core.database import create_user_in_org, list_org_users, set_user_active
from app.core.security import hash_password, require_admin, validate_password_strength

router = APIRouter()


class CreateUserRequest(BaseModel):
    username: str
    email: EmailStr
    password: str
    role: str = "member"

    @field_validator("username")
    @classmethod
    def check_username(cls, v: str) -> str:
        v = v.strip().lower()
        if not re.fullmatch(r"[a-z0-9_.-]{3,50}", v):
            raise ValueError("Username must be 3–50 chars: letters, digits, . _ -")
        return v

    @field_validator("role")
    @classmethod
    def check_role(cls, v: str) -> str:
        if v not in ("admin", "member"):
            raise ValueError("role must be 'admin' or 'member'")
        return v

    @field_validator("password")
    @classmethod
    def check_password(cls, v: str) -> str:
        return validate_password_strength(v)


class ActiveRequest(BaseModel):
    is_active: bool


@router.get("/")
def list_users(admin: Annotated[dict, Depends(require_admin)]):
    return {"users": list_org_users(admin.get("org_id", -1))}


@router.post("/", status_code=201)
def create_user(req: CreateUserRequest, admin: Annotated[dict, Depends(require_admin)]):
    try:
        create_user_in_org(
            org_id=admin.get("org_id", -1),
            username=req.username,
            email=str(req.email).lower(),
            password_hash=hash_password(req.password),
            role=req.role,
        )
    except ValueError as exc:
        if str(exc) == "conflict":
            raise HTTPException(status.HTTP_409_CONFLICT, "Username or email already exists")
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid user data")
    return {"username": req.username, "role": req.role, "detail": "User created"}


@router.patch("/{username}/status")
def update_user_status(
    username: str, req: ActiveRequest, admin: Annotated[dict, Depends(require_admin)]
):
    if username == admin.get("sub"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot change your own status")
    updated = set_user_active(admin.get("org_id", -1), username.lower(), req.is_active)
    if not updated:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found in your organization")
    return {"username": username.lower(), "is_active": req.is_active}
