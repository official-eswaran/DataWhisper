"""Multi-tenancy + user management + registration."""
import io
import uuid

import pytest

CSV = b"region,revenue\nNorth,100\nSouth,250\n"


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def stub_llm(monkeypatch):
    monkeypatch.setattr(
        "app.nl2sql.pipeline.call_local_llm",
        lambda prompt: "SELECT SUM(revenue) AS total_revenue FROM sales",
    )


def _register(client, org, user):
    return client.post("/api/auth/register", json={
        "org_name": org, "username": user, "email": f"{user}@{org}.io", "password": "Str0ngPass1",
    })


def _upload(client, token) -> str:
    files = {"file": ("sales.csv", io.BytesIO(CSV), "text/csv")}
    r = client.post("/api/upload/", headers=_auth(token), files=files)
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


def test_registration_creates_org_owner(client):
    r = _register(client, "acme", "acmeowner")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["role"] == "owner"
    assert "access_token" in body and "refresh_token" in body


def test_registration_rejects_weak_password(client):
    r = client.post("/api/auth/register", json={
        "org_name": "weakco", "username": "weakuser", "email": "w@weakco.io", "password": "short",
    })
    assert r.status_code == 422


def test_registration_conflict(client):
    _register(client, "dupco", "dupuser")
    again = _register(client, "dupco2", "dupuser")  # same username
    assert again.status_code == 409


def test_cross_tenant_session_isolation(client):
    """A user in org A must not access a session created in org B, even though
    they are an owner in their own org."""
    owner_a = _register(client, "orga", "ownera").json()["access_token"]
    owner_b = _register(client, "orgb", "ownerb").json()["access_token"]

    session_b = _upload(client, owner_b)

    # Owner A (different org) is denied — 404, no leak.
    denied = client.post("/api/query/", headers=_auth(owner_a),
                         json={"session_id": session_b, "question": "total revenue"})
    assert denied.status_code == 404

    # Owner B can query their own session.
    ok = client.post("/api/query/", headers=_auth(owner_b),
                     json={"session_id": session_b, "question": "total revenue"})
    assert ok.status_code == 200


def test_audit_logs_are_tenant_scoped(client):
    owner_a = _register(client, "auditorga", "audita").json()["access_token"]
    owner_b = _register(client, "auditorgb", "auditb").json()["access_token"]

    sa = _upload(client, owner_a)
    client.post("/api/query/", headers=_auth(owner_a),
                json={"session_id": sa, "question": "unique-a-question total revenue"})

    # Org B's admin must not see org A's audit entries.
    logs_b = client.get("/api/audit/logs", headers=_auth(owner_b)).json()
    assert all("unique-a-question" not in (i["question"] or "") for i in logs_b["items"])

    logs_a = client.get("/api/audit/logs", headers=_auth(owner_a)).json()
    assert any("unique-a-question" in (i["question"] or "") for i in logs_a["items"])


def test_user_management_flow(client):
    owner = _register(client, "usermgmtco", "umowner").json()["access_token"]

    # Create a member.
    created = client.post("/api/users/", headers=_auth(owner), json={
        "username": "member1", "email": "m1@usermgmtco.io", "password": "MemberPass1", "role": "member",
    })
    assert created.status_code == 201, created.text

    # It appears in the org user list.
    listed = client.get("/api/users/", headers=_auth(owner)).json()
    assert any(u["username"] == "member1" for u in listed["users"])

    # New member can log in and query, but cannot read audit logs.
    login = client.post("/api/auth/login", json={"username": "member1", "password": "MemberPass1"})
    assert login.status_code == 200
    member_token = login.json()["access_token"]
    assert client.get("/api/audit/logs", headers=_auth(member_token)).status_code == 403

    # Owner deactivates the member → login now blocked.
    client.patch("/api/users/member1/status", headers=_auth(owner), json={"is_active": False})
    blocked = client.post("/api/auth/login", json={"username": "member1", "password": "MemberPass1"})
    assert blocked.status_code == 403


def test_member_cannot_manage_users(client):
    owner = _register(client, "rbacco", "rbacowner").json()["access_token"]
    client.post("/api/users/", headers=_auth(owner), json={
        "username": "plain", "email": "p@rbacco.io", "password": "PlainPass12", "role": "member",
    })
    member = client.post("/api/auth/login", json={"username": "plain", "password": "PlainPass12"}).json()["access_token"]
    resp = client.post("/api/users/", headers=_auth(member), json={
        "username": "x", "email": "x@rbacco.io", "password": "AnotherPass1", "role": "member",
    })
    assert resp.status_code == 403


def test_unauthenticated_still_rejected(client):
    assert client.get("/api/users/").status_code in (401, 403)
    assert client.post("/api/query/", json={"session_id": str(uuid.uuid4()), "question": "x"}).status_code in (401, 403)
