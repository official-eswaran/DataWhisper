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


def test_pdf_cell_is_not_truncated_at_60_chars():
    """#46 regression: the export is the compliance artifact, so the generated
    SQL must survive intact — the old ``_safe(val)[:60]`` cut every WHERE clause."""
    from app.api.routes.export import CELL_TEXT_LIMIT, _safe

    long_sql = (
        "SELECT region, SUM(revenue) AS total FROM sales "
        "WHERE year = 2024 AND category = 'Electronics' "
        "GROUP BY region ORDER BY total DESC LIMIT 100"
    )
    assert len(long_sql) > 60
    assert _safe(long_sql) == long_sql  # no clipping of real statements
    # The runaway guard still applies far above any legitimate length.
    assert len(_safe("x" * (CELL_TEXT_LIMIT + 5000))) == CELL_TEXT_LIMIT


def test_build_session_pdf_survives_long_sql_and_markup():
    """The wrapping cells must handle SQL with <, >, & and newlines without
    raising (escaping bug would 500 the export route)."""
    from app.api.routes.export import build_session_pdf

    tricky_sql = (
        "SELECT region, SUM(revenue) AS total\n"
        "FROM sales\n"
        "WHERE revenue > 100 AND flag < 5 AND note = 'a & b'\n"
        "GROUP BY region ORDER BY total DESC LIMIT 50 -- " + "x" * 300
    )
    rows = [(
        "What is the total revenue by region for Electronics in 2024?",
        tricky_sql,
        "North: 100; South: 250; East: 175",
        "2024-01-01 10:00:00",
    )]
    pdf = build_session_pdf("sess-abc-123", rows)
    assert pdf[:4] == b"%PDF"
    assert len(pdf) > 1000  # actually rendered content, not an empty shell


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
    body = login.json()
    assert "access_token" in body
    # The refresh token is now an httpOnly cookie, never in the JSON body (#22).
    assert "refresh_token" not in body
    old_cookie = login.cookies.get("dw_refresh")
    assert old_cookie, "login must set the httpOnly refresh cookie"

    # Refresh rotates to a new pair — the cookie is sent automatically.
    r = client.post("/api/auth/refresh")
    assert r.status_code == 200, r.text
    assert r.json()["access_token"] != body["access_token"]
    assert r.cookies.get("dw_refresh"), "rotation must issue a fresh refresh cookie"

    # The OLD token was revoked on rotation: presenting it explicitly fails.
    client.cookies.set("dw_refresh", old_cookie, path="/api/auth")
    reused = client.post("/api/auth/refresh")
    assert reused.status_code == 401
    client.cookies.clear()  # don't leave the revoked cookie in the shared jar


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
