"""GDPR data-subject rights (M6)."""
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
    }).json()


def _login(client, user, pw):
    r = client.post("/api/auth/login", json={"username": user, "password": pw})
    return r


def _create_member(client, owner_token, org, name):
    return client.post("/api/users/", headers=_auth(owner_token), json={
        "username": name, "email": f"{name}@{org}.io", "password": "MemberPass1", "role": "member",
    })


def _upload(client, token):
    files = {"file": ("sales.csv", io.BytesIO(b"region,revenue\nN,100\nS,250\n"), "text/csv")}
    return client.post("/api/upload/", headers=_auth(token), files=files).json()["session_id"]


def test_export_returns_all_user_data(client):
    owner = _register(client, "expco", "expowner")["access_token"]
    sid = _upload(client, owner)
    client.post("/api/query/", headers=_auth(owner),
                json={"session_id": sid, "question": "total revenue please"})

    resp = client.get("/api/me/export", headers=_auth(owner))
    assert resp.status_code == 200
    assert "attachment" in resp.headers["content-disposition"]
    data = resp.json()
    assert data["profile"]["username"] == "expowner"
    assert any(d["session_id"] == sid for d in data["datasets"])
    assert any("total revenue please" in a["question"] for a in data["activity"])


def test_member_can_delete_own_account(client):
    owner = _register(client, "delco", "delowner")["access_token"]
    _create_member(client, owner, "delco", "delmember")
    member = _login(client, "delmember", "MemberPass1").json()["access_token"]

    sid = _upload(client, member)
    resp = client.delete("/api/me", headers=_auth(member))
    assert resp.status_code == 200
    assert resp.json()["datasets_deleted"] >= 1

    # Account gone → cannot log in, and the dataset was removed.
    assert _login(client, "delmember", "MemberPass1").status_code == 401
    assert client.post("/api/query/", headers=_auth(owner),
                       json={"session_id": sid, "question": "total revenue"}).status_code == 404


def test_sole_owner_cannot_self_delete(client):
    owner = _register(client, "soleco", "soleowner")["access_token"]
    resp = client.delete("/api/me", headers=_auth(owner))
    assert resp.status_code == 409


def test_owner_can_delete_organization(client):
    owner = _register(client, "wipeco", "wipeowner")["access_token"]
    _create_member(client, owner, "wipeco", "wipemember")
    _upload(client, owner)

    resp = client.delete("/api/org", headers=_auth(owner))
    assert resp.status_code == 200
    assert resp.json()["users_deleted"] >= 2

    # Everyone in the org is gone.
    assert _login(client, "wipeowner", "Str0ngPass1").status_code == 401
    assert _login(client, "wipemember", "MemberPass1").status_code == 401


def test_member_cannot_delete_organization(client):
    owner = _register(client, "protectco", "protectowner")["access_token"]
    _create_member(client, owner, "protectco", "protectmember")
    member = _login(client, "protectmember", "MemberPass1").json()["access_token"]
    assert client.delete("/api/org", headers=_auth(member)).status_code == 403
