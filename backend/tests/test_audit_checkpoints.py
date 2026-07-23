"""Bounded audit-chain verification via signed checkpoints (issue #30).

Full verification re-walks every entry, so its cost grows with history. These
tests cover the bounded alternative: verify only what came after the newest
signed checkpoint, and be explicit that that is what happened.
"""
import io

import pytest
from sqlalchemy import update

from app.core import database as db
from app.core.config import settings


def _auth(t):
    return {"Authorization": f"Bearer {t}"}


@pytest.fixture(autouse=True)
def stub_llm(monkeypatch):
    monkeypatch.setattr(
        "app.nl2sql.pipeline.call_local_llm",
        lambda prompt: "SELECT SUM(revenue) AS total_revenue FROM sales",
    )


@pytest.fixture(autouse=True)
def frequent_checkpoints(monkeypatch):
    """Checkpoint every 2 entries so tests don't have to write 1,000 of them."""
    monkeypatch.setattr(settings, "AUDIT_CHECKPOINT_INTERVAL", 2)


def _register(client, org, user):
    return client.post("/api/auth/register", json={
        "org_name": org, "username": user, "email": f"{user}@{org}.io", "password": "Str0ngPass1",
    }).json()["access_token"]


def _org_id(username):
    return db.get_user_by_username(username)["org_id"]


def _queries(client, token, n=4):
    files = {"file": ("sales.csv", io.BytesIO(b"region,revenue\nN,100\nS,250\n"), "text/csv")}
    sid = client.post("/api/upload/", headers=_auth(token), files=files).json()["session_id"]
    for i in range(n):
        client.post("/api/query/", headers=_auth(token),
                    json={"session_id": sid, "question": f"total revenue q{i}"})
    return sid


# ── Checkpoint creation ────────────────────────────────────────────────────

def test_checkpoints_are_written_on_the_interval(client):
    token = _register(client, "ckptco", "ckptowner")
    org_id = _org_id("ckptowner")
    _queries(client, token, n=4)

    checkpoint = db.latest_audit_checkpoint(org_id)
    assert checkpoint is not None
    assert checkpoint["signature_valid"] is True
    assert checkpoint["entries"] % 2 == 0


def test_no_checkpoint_before_the_first_interval(client, monkeypatch):
    monkeypatch.setattr(settings, "AUDIT_CHECKPOINT_INTERVAL", 10_000)
    token = _register(client, "nockptco", "nockptowner")
    _queries(client, token, n=2)
    assert db.latest_audit_checkpoint(_org_id("nockptowner")) is None


def test_interval_zero_disables_checkpointing(client, monkeypatch):
    monkeypatch.setattr(settings, "AUDIT_CHECKPOINT_INTERVAL", 0)
    token = _register(client, "offckptco", "offckptowner")
    _queries(client, token, n=4)
    assert db.latest_audit_checkpoint(_org_id("offckptowner")) is None


# ── Bounded verification ───────────────────────────────────────────────────

def test_incremental_verify_is_bounded_to_entries_after_the_checkpoint(client):
    token = _register(client, "boundco", "boundowner")
    org_id = _org_id("boundowner")
    _queries(client, token, n=5)

    full = db.verify_audit_chain(org_id, full=True)
    incremental = db.verify_audit_chain(org_id, full=False)

    assert full["valid"] and incremental["valid"]
    assert full["scope"] == "full"
    assert incremental["scope"] == "incremental"
    # The whole point: fewer rows re-hashed than the chain contains.
    assert incremental["entries"] < full["entries"]
    assert incremental["verified_from_id"] is not None


def test_incremental_verify_says_what_it_did_not_cover(client):
    """`valid` must never be readable as "all of history is intact"."""
    token = _register(client, "scopeco", "scopeowner")
    org_id = _org_id("scopeowner")
    _queries(client, token, n=5)

    result = db.verify_audit_chain(org_id, full=False)
    assert result["unverified_before_id"] == result["verified_from_id"]
    assert result["checkpoint"]["signature_valid"] is True


def test_falls_back_to_full_when_there_is_no_checkpoint(client, monkeypatch):
    monkeypatch.setattr(settings, "AUDIT_CHECKPOINT_INTERVAL", 10_000)
    token = _register(client, "fallbackco", "fallbackowner")
    org_id = _org_id("fallbackowner")
    _queries(client, token, n=2)

    result = db.verify_audit_chain(org_id, full=False)
    assert result["scope"] == "full"
    assert result["verified_from_id"] is None
    assert result["valid"] is True


def test_incremental_verify_catches_tampering_after_the_checkpoint(client):
    token = _register(client, "recentco", "recentowner")
    org_id = _org_id("recentowner")
    _queries(client, token, n=5)
    anchor = db.latest_audit_checkpoint(org_id)["last_id"]

    with db.get_engine().begin() as conn:
        target = conn.execute(
            db.audit_logs.select()
            .where(db.audit_logs.c.org_id == org_id, db.audit_logs.c.id > anchor)
            .order_by(db.audit_logs.c.id.asc())
        ).first()
        conn.execute(
            update(db.audit_logs).where(db.audit_logs.c.id == target.id)
            .values(natural_query="FORGED")
        )

    result = db.verify_audit_chain(org_id, full=False)
    assert result["valid"] is False
    assert result["broken_at"] == target.id


def test_tampering_before_the_checkpoint_needs_a_full_verify(client):
    """The honest limitation, pinned: bounded verification is bounded."""
    token = _register(client, "oldco", "oldowner")
    org_id = _org_id("oldowner")
    _queries(client, token, n=6)
    anchor = db.latest_audit_checkpoint(org_id)["last_id"]

    with db.get_engine().begin() as conn:
        target = conn.execute(
            db.audit_logs.select()
            .where(db.audit_logs.c.org_id == org_id, db.audit_logs.c.id < anchor)
            .order_by(db.audit_logs.c.id.asc())
        ).first()
        conn.execute(
            update(db.audit_logs).where(db.audit_logs.c.id == target.id)
            .values(natural_query="FORGED LONG AGO")
        )

    assert db.verify_audit_chain(org_id, full=False)["valid"] is True   # bounded: blind
    assert db.verify_audit_chain(org_id, full=True)["valid"] is False   # full: catches it


def test_forged_checkpoint_is_rejected_not_trusted(client):
    """An attacker who rewrites history must also forge the HMAC — they can't."""
    token = _register(client, "forgeco", "forgeowner")
    org_id = _org_id("forgeowner")
    _queries(client, token, n=4)

    with db.get_engine().begin() as conn:
        conn.execute(
            update(db.audit_checkpoints)
            .where(db.audit_checkpoints.c.org_id == org_id)
            .values(last_hash="f" * 64)
        )

    result = db.verify_audit_chain(org_id, full=False)
    assert result["valid"] is False
    assert result["broken_at"] == "checkpoint"


def test_signature_is_keyed_on_the_secret(client, monkeypatch):
    token = _register(client, "keyco", "keyowner")
    org_id = _org_id("keyowner")
    _queries(client, token, n=4)
    assert db.latest_audit_checkpoint(org_id)["signature_valid"] is True

    monkeypatch.setattr(settings, "SECRET_KEY", "y" * 64)
    assert db.latest_audit_checkpoint(org_id)["signature_valid"] is False


# ── On-demand checkpointing ────────────────────────────────────────────────

def test_manual_checkpoint_signs_the_current_head(client):
    token = _register(client, "manualco", "manualowner")
    org_id = _org_id("manualowner")
    _queries(client, token, n=3)

    result = db.write_audit_checkpoint(org_id)
    assert result["created"] is True
    checkpoint = db.latest_audit_checkpoint(org_id)
    assert checkpoint["last_id"] == result["last_id"]
    assert checkpoint["signature_valid"] is True
    # Everything is now behind the anchor, so an incremental verify is trivial.
    assert db.verify_audit_chain(org_id, full=False)["entries"] == 0


def test_manual_checkpoint_refuses_a_broken_chain(client):
    token = _register(client, "brokenco", "brokenowner")
    org_id = _org_id("brokenowner")
    _queries(client, token, n=5)
    anchor = db.latest_audit_checkpoint(org_id)["last_id"]

    with db.get_engine().begin() as conn:
        target = conn.execute(
            db.audit_logs.select()
            .where(db.audit_logs.c.org_id == org_id, db.audit_logs.c.id > anchor)
            .order_by(db.audit_logs.c.id.asc())
        ).first()
        conn.execute(
            update(db.audit_logs).where(db.audit_logs.c.id == target.id).values(status="FORGED")
        )

    result = db.write_audit_checkpoint(org_id)
    assert result["created"] is False
    assert db.latest_audit_checkpoint(org_id)["last_id"] == anchor


def test_checkpoint_on_an_empty_chain_is_a_no_op(client):
    _register(client, "emptyco", "emptyowner")
    result = db.write_audit_checkpoint(_org_id("emptyowner"))
    assert result["created"] is False
    assert "no audit entries" in result["reason"]


# ── HTTP surface ───────────────────────────────────────────────────────────

def test_verify_endpoint_defaults_to_full(client):
    token = _register(client, "apifullco", "apifullowner")
    _queries(client, token, n=4)
    body = client.get("/api/audit/verify", headers=_auth(token)).json()
    assert body["scope"] == "full"
    assert body["valid"] is True


def test_verify_endpoint_supports_incremental(client):
    token = _register(client, "apiincco", "apiincowner")
    _queries(client, token, n=5)
    body = client.get("/api/audit/verify?full=false", headers=_auth(token)).json()
    assert body["scope"] == "incremental"
    assert body["verified_from_id"] is not None


def test_verify_endpoint_since_id_picks_an_earlier_anchor(client):
    token = _register(client, "apisinceco", "apisinceowner")
    org_id = _org_id("apisinceowner")
    _queries(client, token, n=8)

    newest = db.latest_audit_checkpoint(org_id)["last_id"]
    body = client.get(
        f"/api/audit/verify?full=false&since_id={newest - 1}", headers=_auth(token)
    ).json()
    assert body["valid"] is True
    assert body["verified_from_id"] < newest
    assert body["entries"] > db.verify_audit_chain(org_id, full=False)["entries"]


def test_checkpoint_endpoints_require_admin(client, manager_token):
    assert client.get("/api/audit/checkpoint", headers=_auth(manager_token)).status_code == 403
    assert client.post("/api/audit/checkpoint", headers=_auth(manager_token)).status_code == 403


def test_checkpoint_endpoint_creates_and_reports(client):
    token = _register(client, "apickptco", "apickptowner")
    _queries(client, token, n=3)

    created = client.post("/api/audit/checkpoint", headers=_auth(token)).json()
    assert created["created"] is True
    latest = client.get("/api/audit/checkpoint", headers=_auth(token)).json()["checkpoint"]
    assert latest["last_id"] == created["last_id"]
