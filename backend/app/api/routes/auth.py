from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import jwt

from app.core.config import settings

router = APIRouter()

# Simple in-memory user store (replace with DB in production)
USERS = {
    "ceo": {"password": "admin123", "role": "admin"},
    "manager": {"password": "manager123", "role": "department"},
}


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/login")
def login(req: LoginRequest):
    user = USERS.get(req.username)
    if not user or user["password"] != req.password:
        raise HTTPException(401, "Invalid credentials")

    token = jwt.encode(
        {
            "sub": req.username,
            "role": user["role"],
            "exp": datetime.utcnow()
            + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
        },
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )
    return {"access_token": token, "role": user["role"]}
