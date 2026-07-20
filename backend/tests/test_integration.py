"""End-to-end happy path on a clean database.

This is the test whose absence let two crash-on-fresh-install bugs ship:
  * audit-log INSERT into a non-existent ``user_id`` column, and
  * PDF export filtering on that same missing column.
It drives login → upload → query → export → audit, plus the IDOR guard.
The LLM is stubbed so the suite needs no running Ollama.
"""
import io
import uuid

import pytest

CSV = b"region,revenue\nNorth,100\nSouth,250\nEast,175\n"


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def stub_llm(monkeypatch):
    """Force the SQL the 'model' returns; intent stays real (keyword path)."""
    def fake_llm(prompt: str) -> str:
        return "SELECT SUM(revenue) AS total_revenue FROM sales"

    monkeypatch.setattr("app.nl2sql.pipeline.call_local_llm", fake_llm)


def _upload(client, token) -> str:
    files = {"file": ("sales.csv", io.BytesIO(CSV), "text/csv")}
    resp = client.post("/api/upload/", headers=_auth(token), files=files)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["rows"] == 3
    assert body["table_name"] == "sales"
    return body["session_id"]


def test_upload_query_export_flow(client, admin_token):
    session_id = _upload(client, admin_token)

    # Query — exercises pipeline + audit write (Bug #1 regression guard).
    resp = client.post(
        "/api/query/",
        headers=_auth(admin_token),
        json={"session_id": session_id, "question": "What is the total revenue?"},
    )
    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert result["type"] == "single_value"
    assert result["data"][0]["total_revenue"] == 525
    assert result["sql"].lower().startswith("select")

    # Export — Bug #2 regression guard (must not 500).
    resp = client.get(f"/api/export/pdf/{session_id}", headers=_auth(admin_token))
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content[:4] == b"%PDF"

    # Audit logs — admin, paginated envelope.
    resp = client.get("/api/audit/logs?limit=10", headers=_auth(admin_token))
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["total"] >= 1
    assert any(item["question"] == "What is the total revenue?" for item in payload["items"])


def test_idor_other_users_session_is_denied(client, admin_token, manager_token):
    """A user must not query a session they do not own (unless admin)."""
    manager_session = _upload(client, manager_token)

    # Manager owns it → OK.
    ok = client.post(
        "/api/query/",
        headers=_auth(manager_token),
        json={"session_id": manager_session, "question": "total revenue"},
    )
    assert ok.status_code == 200

    # A random non-existent session → 404 (no info leak).
    ghost = str(uuid.uuid4())
    resp = client.post(
        "/api/query/",
        headers=_auth(manager_token),
        json={"session_id": ghost, "question": "total revenue"},
    )
    assert resp.status_code == 404


def test_admin_can_access_any_session(client, admin_token, manager_token):
    manager_session = _upload(client, manager_token)
    resp = client.get(f"/api/export/pdf/{manager_session}", headers=_auth(admin_token))
    assert resp.status_code == 200


def test_manager_cannot_read_audit_logs(client, manager_token):
    resp = client.get("/api/audit/logs", headers=_auth(manager_token))
    assert resp.status_code == 403


def test_unauthenticated_is_rejected(client):
    resp = client.post("/api/query/", json={"session_id": str(uuid.uuid4()), "question": "x"})
    assert resp.status_code in (401, 403)


def test_refresh_and_logout_flow(client):
    login = client.post("/api/auth/login", json={"username": "ceo", "password": "Admin@2024"})
    assert login.status_code == 200
    tokens = login.json()
    assert "refresh_token" in tokens and "access_token" in tokens

    # Refresh rotates to a new pair.
    r = client.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 200
    new_tokens = r.json()
    assert new_tokens["access_token"] != tokens["access_token"]

    # Old refresh token was revoked on rotation.
    reused = client.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert reused.status_code == 401


def test_bad_login_is_401(client):
    resp = client.post("/api/auth/login", json={"username": "ceo", "password": "wrong"})
    assert resp.status_code == 401


def test_oversized_upload_rejected(client, admin_token, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "MAX_UPLOAD_SIZE_MB", 0)  # 0 MB → anything non-empty is too big
    big = b"region,revenue\n" + b"North,100\n" * 1000
    files = {"file": ("big.csv", io.BytesIO(big), "text/csv")}
    resp = client.post("/api/upload/", headers=_auth(admin_token), files=files)
    assert resp.status_code == 413
