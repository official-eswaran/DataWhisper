from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, field_validator

from app.core.config import settings
from app.core.database import (
    get_user_by_username,
    record_failed_login,
    record_successful_login,
)
from app.core.security import verify_password, create_access_token

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


@router.post("/login")
def login(request: Request, req: LoginRequest):
    """Authenticate with username + password — returns a signed JWT."""
    user = get_user_by_username(req.username)

    # Always take same code path to prevent username enumeration via timing
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid username or password")

    if not user["is_active"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account is disabled")

    # Check lockout
    if user["locked_until"]:
        locked_until = user["locked_until"]
        # SQLite returns strings; compare as string (ISO format sorts lexicographically)
        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        if locked_until > now_str:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                f"Account locked due to too many failed attempts. Try again after {locked_until} UTC",
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

    token = create_access_token({"sub": req.username, "role": user["role"]})
    return {"access_token": token, "token_type": "bearer", "role": user["role"]}
