"""Tamper-evident audit chain (M7)."""
import io

import pytest


def _auth(t):
    return {"Authorization": f"Bearer {t}"}


@pytest.fixture(autouse=True)
def stub_llm(monkeypatch):
    monkeypatch.setattr(
        "app.nl2sql.pipeline.call_local_llm",
        lambda prompt: "SELECT SUM(revenue) AS total_revenue FROM sales",
    )


def _register(client, org, user):
    return client.post("/api/auth/register", json={
        "org_name": org, "username": user, "email": f"{user}@{org}.io", "password": "Str0ngPass1",
    }).json()["access_token"]


def _upload_and_query(client, token, n=3):
    files = {"file": ("sales.csv", io.BytesIO(b"region,revenue\nN,100\nS,250\n"), "text/csv")}
    sid = client.post("/api/upload/", headers=_auth(token), files=files).json()["session_id"]
    for i in range(n):
        client.post("/api/query/", headers=_auth(token),
                    json={"session_id": sid, "question": f"total revenue q{i}"})
    return sid


def test_chain_is_valid_after_writes(client):
    token = _register(client, "chainco", "chainowner")
    _upload_and_query(client, token, n=3)
    resp = client.get("/api/audit/verify", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is True
    assert body["entries"] >= 3


def test_tampering_is_detected(client):
    """Directly mutating an audit row must break the chain."""
    from sqlalchemy import update

    from app.core.database import audit_logs, get_engine

    token = _register(client, "tamperco", "tamperowner")
    _upload_and_query(client, token, n=3)

    # Verify healthy first.
    assert client.get("/api/audit/verify", headers=_auth(token)).json()["valid"] is True

    # Tamper: rewrite a stored question (as a malicious admin / DB breach would).
    with get_engine().begin() as conn:
        row = conn.execute(audit_logs.select().where(audit_logs.c.username == "tamperowner")).first()
        conn.execute(
            update(audit_logs).where(audit_logs.c.id == row.id)
            .values(natural_query="FORGED QUESTION")
        )

    verified = client.get("/api/audit/verify", headers=_auth(token)).json()
    assert verified["valid"] is False
    assert verified["broken_at"] is not None


def test_chains_are_isolated_per_org(client):
    a = _register(client, "isoa", "isoaowner")
    b = _register(client, "isob", "isobowner")
    _upload_and_query(client, a, n=2)
    _upload_and_query(client, b, n=2)
    assert client.get("/api/audit/verify", headers=_auth(a)).json()["valid"] is True
    assert client.get("/api/audit/verify", headers=_auth(b)).json()["valid"] is True


def test_verify_requires_admin(client, manager_token):
    # 'manager' is a member of the demo org → cannot verify.
    assert client.get("/api/audit/verify", headers=_auth(manager_token)).status_code == 403
