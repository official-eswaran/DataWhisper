"""Demo-account seeding is gated so production never ships known creds (#23).

init_db used to seed ceo/manager (default passwords) into *every* empty
database. These tests pin the gate: seeding follows DEBUG unless SEED_DEMO_DATA
says otherwise, so a fresh production DB (DEBUG=false) gets no accounts.
"""
from sqlalchemy import create_engine, func, select

from app.core import database
from app.core.config import settings
from app.core.database import users


def _fresh_engine(tmp_path):
    """An isolated empty SQLite engine, so init_db can't touch the shared test DB."""
    return create_engine(
        f"sqlite:///{tmp_path}/seed.db", connect_args={"check_same_thread": False}
    )


# ── The auto/override decision ─────────────────────────────────────────────

def test_auto_follows_debug(monkeypatch):
    monkeypatch.setattr(settings, "SEED_DEMO_DATA", None)
    monkeypatch.setattr(settings, "DEBUG", True)
    assert settings.should_seed_demo is True
    monkeypatch.setattr(settings, "DEBUG", False)
    assert settings.should_seed_demo is False


def test_explicit_flag_overrides_debug(monkeypatch):
    # Explicit True seeds even in prod; explicit False stays off even in dev.
    monkeypatch.setattr(settings, "DEBUG", False)
    monkeypatch.setattr(settings, "SEED_DEMO_DATA", True)
    assert settings.should_seed_demo is True
    monkeypatch.setattr(settings, "DEBUG", True)
    monkeypatch.setattr(settings, "SEED_DEMO_DATA", False)
    assert settings.should_seed_demo is False


# ── init_db honours the gate ───────────────────────────────────────────────

def test_empty_prod_db_gets_no_accounts(tmp_path, monkeypatch):
    """DEBUG=false, auto → no demo users. This is the whole point of #23."""
    eng = _fresh_engine(tmp_path)
    monkeypatch.setattr(database, "_engine", eng)
    monkeypatch.setattr(settings, "DEBUG", False)
    monkeypatch.setattr(settings, "SEED_DEMO_DATA", None)

    database.init_db()

    with eng.connect() as conn:
        n = conn.execute(select(func.count()).select_from(users)).scalar()
    assert n == 0


def test_seeding_when_enabled_creates_ceo_and_manager(tmp_path, monkeypatch):
    eng = _fresh_engine(tmp_path)
    monkeypatch.setattr(database, "_engine", eng)
    monkeypatch.setattr(settings, "SEED_DEMO_DATA", True)

    database.init_db()

    with eng.connect() as conn:
        names = {r[0] for r in conn.execute(select(users.c.username)).all()}
    assert names == {"ceo", "manager"}


def test_disabled_seeding_leaves_db_usable_for_registration(tmp_path, monkeypatch):
    """No seed ≠ broken: the schema still exists, ready for the first register."""
    eng = _fresh_engine(tmp_path)
    monkeypatch.setattr(database, "_engine", eng)
    monkeypatch.setattr(settings, "SEED_DEMO_DATA", False)

    database.init_db()

    # Tables were created; a subsequent insert path has somewhere to go.
    with eng.connect() as conn:
        assert conn.execute(select(func.count()).select_from(users)).scalar() == 0


def test_warns_when_seeding_prod_with_default_passwords(tmp_path, monkeypatch, caplog):
    """Opting into prod seeding with the built-in passwords must warn loudly."""
    eng = _fresh_engine(tmp_path)
    monkeypatch.setattr(database, "_engine", eng)
    monkeypatch.setattr(settings, "DEBUG", False)
    monkeypatch.setattr(settings, "SEED_DEMO_DATA", True)
    monkeypatch.setattr(settings, "ADMIN_PASSWORD", "Admin@2024")

    with caplog.at_level("WARNING"):
        database.init_db()

    assert any("DEFAULT passwords" in r.message for r in caplog.records)
