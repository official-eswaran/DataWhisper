"""Authentication endpoints: login, refresh, logout.

* Login timing is uniform whether or not the username exists (anti-enumeration).
* Access tokens are short-lived; refresh tokens are server-tracked and revocable.
* Refresh rotates: using a refresh token revokes it and issues a new pair, so a
  stolen-and-replayed refresh token is caught on the next legitimate use.

NOTE: no ``from __future__ import annotations`` — combined with the slowapi
decorator it makes FastAPI misclassify the Pydantic body as a query parameter.
"""
import re
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr, field_validator

from app.core.config import settings
from app.core.database import (
    create_organization_with_owner,
    get_user_by_username,
    is_refresh_token_valid,
    record_failed_login,
    record_successful_login,
    revoke_all_user_tokens,
    revoke_refresh_token,
    store_refresh_token,
)
from app.core.ratelimit import limiter
from app.core.security import (
    TOKEN_TYPE_REFRESH,
    bearer_scheme,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    validate_password_strength,
    verify_dummy_password,
    verify_password,
)

router = APIRouter()


class LoginRequest(BaseModel):
    username: str
    password: str

    @field_validator("username")
    @classmethod
    def clean_username(cls, v: str) -> str:
        v = v.strip().lower()
        if not v or len(v) > 50:
            raise ValueError("Invalid username")
        return v

    @field_validator("password")
    @classmethod
    def check_password(cls, v: str) -> str:
        if not v or len(v) > 128:
            raise ValueError("Invalid password")
        return v


class RefreshRequest(BaseModel):
    refresh_token: str


class RegisterRequest(BaseModel):
    org_name: str
    username: str
    email: EmailStr
    password: str

    @field_validator("org_name")
    @classmethod
    def check_org(cls, v: str) -> str:
        v = v.strip()
        if not (2 <= len(v) <= 120):
            raise ValueError("Organization name must be 2–120 characters")
        return v

    @field_validator("username")
    @classmethod
    def check_username(cls, v: str) -> str:
        v = v.strip().lower()
        if not re.fullmatch(r"[a-z0-9_.-]{3,50}", v):
            raise ValueError("Username must be 3–50 chars: letters, digits, . _ -")
        return v

    @field_validator("password")
    @classmethod
    def check_password_strength(cls, v: str) -> str:
        return validate_password_strength(v)


def _issue_tokens(username: str, role: str, org_id: int) -> dict:
    access, expires_in = create_access_token(username, role, org_id)
    refresh, jti, expires_at = create_refresh_token(username, role, org_id)
    store_refresh_token(jti, username, org_id, expires_at)
    return {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "bearer",
        "expires_in": expires_in,
        "role": role,
    }


@router.post("/register", status_code=201)
@limiter.limit(settings.RATE_LIMIT_LOGIN)
def register(request: Request, req: RegisterRequest):
    """Self-service signup — creates a new organization and its owner user."""
    try:
        created = create_organization_with_owner(
            org_name=req.org_name,
            username=req.username,
            email=str(req.email).lower(),
            password_hash=hash_password(req.password),
        )
    except ValueError:
        raise HTTPException(status.HTTP_409_CONFLICT, "Username or email already exists")
    return _issue_tokens(created["username"], created["role"], created["org_id"])


@router.post("/login")
@limiter.limit(settings.RATE_LIMIT_LOGIN)
def login(request: Request, req: LoginRequest):
    user = get_user_by_username(req.username)

    if user is None:
        # Do the same bcrypt work as a real verify so timing does not reveal
        # whether the username exists.
        verify_dummy_password()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid username or password")

    if not user["is_active"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account is disabled")

    locked_until = user["locked_until"]
    if locked_until:
        if locked_until.tzinfo is None:
            locked_until = locked_until.replace(tzinfo=UTC)
        if locked_until > datetime.now(UTC):
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Account locked due to too many failed attempts. Try again later.",
            )

    if not verify_password(req.password, user["password_hash"]):
        record_failed_login(req.username)
        remaining = settings.MAX_LOGIN_ATTEMPTS - (user["failed_attempts"] + 1)
        if remaining <= 0:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                f"Invalid credentials. Account locked for {settings.LOCKOUT_MINUTES} minutes.",
            )
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            f"Invalid username or password ({remaining} attempt(s) remaining)",
        )

    record_successful_login(req.username)
    return _issue_tokens(req.username, user["role"], user["org_id"])


@router.post("/refresh")
@limiter.limit(settings.RATE_LIMIT_LOGIN)
def refresh(request: Request, req: RefreshRequest):
    payload = decode_token(req.refresh_token, TOKEN_TYPE_REFRESH)
    jti = payload.get("jti", "")
    if not is_refresh_token_valid(jti):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Refresh token is no longer valid")

    # Confirm the account still exists and is active.
    user = get_user_by_username(payload.get("sub", ""))
    if user is None or not user["is_active"]:
        revoke_refresh_token(jti)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account is unavailable")

    # Rotate: revoke the presented token, issue a fresh pair.
    revoke_refresh_token(jti)
    return _issue_tokens(user["username"], user["role"], user["org_id"])


@router.post("/logout")
def logout(credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer_scheme)]):
    """Revoke all of the caller's refresh tokens. Accepts an access token."""
    from app.core.security import TOKEN_TYPE_ACCESS

    payload = decode_token(credentials.credentials, TOKEN_TYPE_ACCESS)
    revoke_all_user_tokens(payload.get("sub", ""))
    return {"detail": "Logged out"}
