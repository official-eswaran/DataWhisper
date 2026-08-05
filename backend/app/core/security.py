"""Authentication primitives: password hashing and JWT issue/verify.

Tokens carry a ``jti`` (unique id) and ``type`` claim. Refresh tokens are
tracked server-side (see :mod:`app.core.database`) so they can be revoked —
plain stateless JWTs cannot be invalidated before expiry, which is unacceptable
for a product handling private business data.
"""
from __future__ import annotations

import base64
import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import settings

bearer_scheme = HTTPBearer(auto_error=True)

# A constant, well-known bcrypt hash of a random string. Verifying against it on
# the "user not found" path keeps login timing uniform (anti-enumeration).
_DUMMY_HASH = bcrypt.hashpw(b"dummy-password-for-timing", bcrypt.gensalt(rounds=12)).decode()

TOKEN_TYPE_ACCESS = "access"
TOKEN_TYPE_REFRESH = "refresh"


def _prehash(password: str) -> bytes:
    """bcrypt silently truncates input at 72 bytes. Pre-hashing with SHA-256
    (base64-encoded to stay in bcrypt's byte budget) removes that ceiling so the
    full password always contributes entropy."""
    digest = hashlib.sha256(password.encode("utf-8")).digest()
    return base64.b64encode(digest)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_prehash(password), bcrypt.gensalt(rounds=12)).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_prehash(plain), hashed.encode())
    except (ValueError, TypeError):
        return False


def verify_dummy_password() -> None:
    """Burn the same CPU as a real verify on the not-found path."""
    bcrypt.checkpw(_prehash("dummy"), _DUMMY_HASH.encode())


def validate_password_strength(password: str) -> str:
    """Enforce a minimum password policy at registration / user creation.
    Returns the password unchanged or raises ValueError."""
    if len(password) < 10 or len(password) > 128:
        raise ValueError("Password must be between 10 and 128 characters")
    if not any(c.isalpha() for c in password):
        raise ValueError("Password must contain a letter")
    if not any(c.isdigit() for c in password):
        raise ValueError("Password must contain a digit")
    return password


def _now() -> datetime:
    return datetime.now(UTC)


def create_access_token(sub: str, role: str, org_id: int) -> tuple[str, int]:
    """Return (token, expires_in_seconds)."""
    expire = _now() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": sub,
        "role": role,
        "org_id": org_id,
        "type": TOKEN_TYPE_ACCESS,
        "jti": uuid.uuid4().hex,
        "iat": _now(),
        "exp": expire,
    }
    token = jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return token, settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60


def create_refresh_token(sub: str, role: str, org_id: int) -> tuple[str, str, datetime]:
    """Return (token, jti, expires_at). The jti is persisted so the token can be
    revoked on logout or rotation."""
    jti = uuid.uuid4().hex
    expire = _now() + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    payload = {
        "sub": sub,
        "role": role,
        "org_id": org_id,
        "type": TOKEN_TYPE_REFRESH,
        "jti": jti,
        "iat": _now(),
        "exp": expire,
    }
    token = jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return token, jti, expire


def decode_token(token: str, expected_type: str) -> dict:
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token has expired — please log in again")
    except jwt.InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid authentication token")

    if payload.get("type") != expected_type:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong token type")
    return payload


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer_scheme)],
) -> dict:
    """FastAPI dependency — validates the Bearer access token, returns its payload."""
    return decode_token(credentials.credentials, TOKEN_TYPE_ACCESS)


def require_admin(user: Annotated[dict, Depends(get_current_user)]) -> dict:
    # Org owners and admins have administrative rights within their organization.
    if user.get("role") not in ("owner", "admin"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin access required")
    return user


def require_verified_email(user: Annotated[dict, Depends(get_current_user)]) -> dict:
    """Block the expensive endpoints until the org's owner confirms their address.

    Issue #21. Applied to *queries*, not to login or registration: the account
    must still be reachable so its owner can land in the UI, read why they are
    blocked and ask for a resend. Gating registration itself would only move the
    dead end earlier.

    Inert when ``should_require_email_verification`` is false, which is the
    default under DEBUG — dev and the test suite are unaffected without opting
    out of anything.
    """
    if not settings.should_require_email_verification:
        return user

    # Imported here rather than at module scope: app.core.database imports from
    # this module for password hashing, and a top-level import would cycle.
    from app.core.database import is_org_email_verified

    if not is_org_email_verified(user.get("org_id", -1)):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Confirm your email address before running queries. "
            "Request a new link at POST /api/auth/verify-email/resend.",
        )
    return user
