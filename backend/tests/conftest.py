"""Test configuration — must set env before any ``app`` import so the settings
singleton picks up isolated temp dirs and a valid secret."""
import os
import pathlib
import tempfile

_tmp = tempfile.mkdtemp(prefix="datawhisper-test-")
os.environ.setdefault("SECRET_KEY", "x" * 64)
os.environ.setdefault("DEBUG", "true")
os.environ.setdefault("DATABASE_DIR", str(pathlib.Path(_tmp) / "db"))
os.environ.setdefault("UPLOAD_DIR", str(pathlib.Path(_tmp) / "up"))
os.environ.setdefault("ALLOWED_ORIGINS", "*")
# Raise rate limits so multi-call test flows don't trip the limiter.
os.environ.setdefault("RATE_LIMIT_LOGIN", "1000/minute")
os.environ.setdefault("RATE_LIMIT_QUERY", "1000/minute")
os.environ.setdefault("RATE_LIMIT_UPLOAD", "1000/minute")
os.environ.setdefault("RATE_LIMIT_REGISTER", "1000/minute")

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="session")
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def admin_token(client):
    resp = client.post("/api/auth/login", json={"username": "ceo", "password": "Admin@2024"})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


@pytest.fixture(scope="session")
def manager_token(client):
    resp = client.post("/api/auth/login", json={"username": "manager", "password": "Manager@2024"})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]
