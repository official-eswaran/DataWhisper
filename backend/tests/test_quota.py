"""Per-tenant quotas + usage metering (issue #6)."""
import io
import uuid

import pytest
from fastapi import HTTPException

from app.core import quota
from app.core.database import create_organization_with_owner, get_usage

CSV = b"region,revenue\nNorth,100\nSouth,250\n"


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _init_db(client):
    """Depend on the app client so init_db() has created the schema before the
    unit tests touch the DB directly."""


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


def _new_org() -> int:
    suffix = uuid.uuid4().hex[:8]
    org = create_organization_with_owner(
        f"quota-{suffix}", f"user-{suffix}", f"user-{suffix}@x.io", "hash"
    )
    return org["org_id"]


# ── Unit: metering + enforcement ───────────────────────────────────────────

def test_record_and_get_usage_increment():
    org_id = _new_org()
    period = quota.current_period()
    quota.record(org_id, quota.QUERIES, 1)
    quota.record(org_id, quota.QUERIES, 2)
    quota.record(org_id, quota.ROWS_PROCESSED, 50)
    usage = get_usage(org_id, period)
    assert usage[quota.QUERIES] == 3
    assert usage[quota.ROWS_PROCESSED] == 50


def test_enforce_quota_blocks_at_limit(monkeypatch):
    org_id = _new_org()
    monkeypatch.setitem(quota.PLAN_LIMITS["free"], quota.UPLOADS, 2)

    quota.enforce_quota(org_id, quota.UPLOADS)      # 0 used — allowed
    quota.record(org_id, quota.UPLOADS, 2)          # now at the limit
    with pytest.raises(HTTPException) as exc:
        quota.enforce_quota(org_id, quota.UPLOADS)
    assert exc.value.status_code == 429


def test_enterprise_plan_is_unlimited(monkeypatch):
    org_id = _new_org()
    from app.core.database import set_org_plan

    set_org_plan(org_id, "enterprise")
    quota.record(org_id, quota.QUERIES, 10_000_000)
    quota.enforce_quota(org_id, quota.QUERIES)      # never raises when unlimited


def test_usage_summary_shape():
    org_id = _new_org()
    quota.record(org_id, quota.QUERIES, 5)
    summary = quota.usage_summary(org_id)
    assert summary["plan"] == "free"
    q = summary["metrics"][quota.QUERIES]
    assert q["used"] == 5
    assert q["limit"] == quota.PLAN_LIMITS["free"][quota.QUERIES]
    assert q["remaining"] == q["limit"] - 5


# ── Integration: endpoints meter and enforce ───────────────────────────────

def test_usage_endpoint_reflects_activity(client):
    tok = _register(client, "usageorg", "usageowner").json()["access_token"]
    sid = _upload(client, tok)
    r = client.post("/api/query/", headers=_auth(tok),
                    json={"session_id": sid, "question": "total revenue?"})
    assert r.status_code == 200, r.text

    body = client.get("/api/usage/", headers=_auth(tok)).json()
    assert body["plan"] == "free"
    assert body["metrics"]["uploads"]["used"] == 1
    assert body["metrics"]["queries"]["used"] == 1
    assert body["metrics"]["rows_processed"]["used"] >= 1


def test_upload_blocked_when_over_quota(client, monkeypatch):
    tok = _register(client, "quotacap", "quotacapowner").json()["access_token"]
    monkeypatch.setitem(quota.PLAN_LIMITS["free"], quota.UPLOADS, 1)
    _upload(client, tok)  # first upload consumes the only slot
    files = {"file": ("sales.csv", io.BytesIO(CSV), "text/csv")}
    r = client.post("/api/upload/", headers=_auth(tok), files=files)
    assert r.status_code == 429


def test_owner_can_change_plan(client):
    tok = _register(client, "planorg", "planowner").json()["access_token"]
    r = client.put("/api/usage/plan", headers=_auth(tok), json={"plan": "pro"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["plan"] == "pro"
    assert body["metrics"]["queries"]["limit"] == quota.PLAN_LIMITS["pro"][quota.QUERIES]


def test_invalid_plan_rejected(client):
    tok = _register(client, "planorg3", "planowner3").json()["access_token"]
    r = client.put("/api/usage/plan", headers=_auth(tok), json={"plan": "platinum"})
    assert r.status_code == 422


def test_member_cannot_change_plan(client):
    owner = _register(client, "planorg2", "planowner2").json()["access_token"]
    client.post("/api/users/", headers=_auth(owner), json={
        "username": "planmember", "email": "m@planorg2.io",
        "password": "Str0ngPass1", "role": "member",
    })
    mtok = client.post("/api/auth/login", json={
        "username": "planmember", "password": "Str0ngPass1",
    }).json()["access_token"]
    r = client.put("/api/usage/plan", headers=_auth(mtok), json={"plan": "pro"})
    assert r.status_code == 403
