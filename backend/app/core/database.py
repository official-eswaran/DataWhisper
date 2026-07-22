"""Persistence layer (SQLAlchemy Core — runs on SQLite or Postgres).

Multi-tenant model:
    organizations 1──* users
    organizations 1──* sessions   (uploaded datasets)
    organizations 1──* audit_logs
Every user, session, and audit row is scoped to an ``org_id``. Authorization
checks require both ownership/role AND matching org, so tenants are isolated.

The metadata store is chosen by ``settings.database_url``:
* SQLite file (default) for dev/single-node,
* Postgres for production multi-node HA.

DuckDB still holds each uploaded dataset (one file per session); those are
independent of this metadata DB.
"""
from __future__ import annotations

import hashlib
import logging
import re
import secrets
from datetime import UTC, datetime, timedelta

import duckdb
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    delete,
    func,
    insert,
    select,
    update,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.core.dataset_storage import dataset_storage

logger = logging.getLogger("datawhisper.database")

metadata = MetaData()

organizations = Table(
    "organizations", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", String(120), nullable=False),
    Column("slug", String(140), nullable=False, unique=True),
    Column("plan", String(20), nullable=False, default="free"),  # free|pro|enterprise
    Column("is_active", Boolean, nullable=False, default=True),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
    # ── Stripe billing linkage (issue #5). Empty until the org first checks out.
    Column("stripe_customer_id", String(64), nullable=False, default=""),
    Column("stripe_subscription_id", String(64), nullable=False, default=""),
    # Mirrors the Stripe subscription status: active|trialing|past_due|canceled.
    # "none" means the org has never subscribed (i.e. it is on free).
    Column("billing_status", String(20), nullable=False, default="none"),
)

users = Table(
    "users", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("org_id", Integer, ForeignKey("organizations.id"), nullable=False),
    Column("username", String(50), nullable=False, unique=True),
    Column("email", String(255), nullable=False, unique=True),
    Column("password_hash", String(255), nullable=False),
    Column("role", String(20), nullable=False, default="member"),  # owner|admin|member
    Column("is_active", Boolean, nullable=False, default=True),
    Column("failed_attempts", Integer, nullable=False, default=0),
    Column("locked_until", DateTime(timezone=True)),
    Column("last_login", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
)

sessions = Table(
    "sessions", metadata,
    Column("session_id", String(36), primary_key=True),
    Column("org_id", Integer, ForeignKey("organizations.id"), nullable=False),
    Column("owner_username", String(50), nullable=False),
    Column("table_name", String(255), nullable=False),
    Column("row_count", Integer, nullable=False, default=0),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
)

audit_logs = Table(
    "audit_logs", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("org_id", Integer, ForeignKey("organizations.id"), nullable=False),
    Column("username", String(50), nullable=False, default=""),
    Column("session_id", String(36)),
    Column("natural_query", Text, nullable=False),
    Column("generated_sql", Text),
    Column("result_summary", Text),
    Column("status", String(20), default="success"),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
    # ── Tamper-evidence (hash chain, per organization) ────────────────────────
    Column("event_ts", String(32), nullable=False, default=""),   # hashed timestamp
    Column("prev_hash", String(64), nullable=False, default=""),
    Column("entry_hash", String(64), nullable=False, default=""),
)

# Per-org head of the audit hash chain. Locked on write to serialize appends.
audit_chain_state = Table(
    "audit_chain_state", metadata,
    Column("org_id", Integer, primary_key=True),
    Column("last_hash", String(64), nullable=False),
    Column("updated_at", DateTime(timezone=True), server_default=func.now()),
)

refresh_tokens = Table(
    "refresh_tokens", metadata,
    Column("jti", String(64), primary_key=True),
    Column("username", String(50), nullable=False),
    Column("org_id", Integer, nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("revoked", Boolean, nullable=False, default=False),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
)

# Per-tenant usage metering. One row per (org, period, metric); the count is
# upserted/incremented as usage happens. period is "YYYY-MM" (monthly buckets).
usage_counters = Table(
    "usage_counters", metadata,
    Column("org_id", Integer, ForeignKey("organizations.id"), primary_key=True),
    Column("period", String(7), primary_key=True),        # e.g. "2026-07"
    Column("metric", String(32), primary_key=True),       # queries|uploads|rows_processed
    Column("count", BigInteger, nullable=False, default=0),
    Column("updated_at", DateTime(timezone=True), server_default=func.now()),
)

# Stripe webhook idempotency. Stripe retries deliveries until it gets a 2xx and
# can deliver the same event more than once; we insert the event id here and
# skip anything already recorded.
stripe_events = Table(
    "stripe_events", metadata,
    Column("event_id", String(64), primary_key=True),
    Column("event_type", String(64), nullable=False, default=""),
    Column("received_at", DateTime(timezone=True), server_default=func.now()),
)

# Performance indexes for the hot query paths.
Index("ix_users_org", users.c.org_id)
Index("ix_sessions_org", sessions.c.org_id)
Index("ix_sessions_created", sessions.c.created_at)
Index("ix_audit_org_created", audit_logs.c.org_id, audit_logs.c.created_at)
Index("ix_audit_session", audit_logs.c.session_id)
Index("ix_refresh_user", refresh_tokens.c.username)
Index("ix_refresh_expires", refresh_tokens.c.expires_at)

VALID_ROLES = {"owner", "admin", "member"}
ADMIN_ROLES = {"owner", "admin"}

# ── Audit hash chain ──────────────────────────────────────────────────────────
GENESIS_HASH = "0" * 64


def _audit_entry_hash(
    prev_hash: str, org_id: int, username: str, session_id: str | None,
    natural_query: str, generated_sql: str, result_summary: str, status: str, event_ts: str,
) -> str:
    """Deterministic hash linking each audit entry to the previous one. Any edit,
    reorder, insertion, or deletion breaks the chain and is detectable."""
    payload = "\x1f".join([
        prev_hash, str(org_id), username or "", session_id or "",
        natural_query or "", generated_sql or "", result_summary or "", status or "", event_ts,
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ── Engine ────────────────────────────────────────────────────────────────────

_engine: Engine | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        url = settings.database_url
        if url.startswith("sqlite"):
            settings.DATABASE_DIR.mkdir(parents=True, exist_ok=True)
            _engine = create_engine(
                url,
                connect_args={"check_same_thread": False},
                pool_pre_ping=True,
            )
            _enable_sqlite_pragmas(_engine)
        else:
            _engine = create_engine(url, pool_pre_ping=True, pool_size=10, max_overflow=20)
    return _engine


def _enable_sqlite_pragmas(engine: Engine) -> None:
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.close()


def _now() -> datetime:
    return datetime.now(UTC)


def ping_db() -> bool:
    with get_engine().connect() as conn:
        return conn.execute(select(1)).scalar() == 1


# ── Schema init + seed ────────────────────────────────────────────────────────

def init_db() -> None:
    """Create tables (idempotent) and seed the demo org on an empty database.

    In production, Alembic (`alembic upgrade head`) is the source of truth for
    schema changes; ``create_all`` here is a safe no-op once tables exist.
    """
    engine = get_engine()
    metadata.create_all(engine)
    with engine.begin() as conn:
        has_users = conn.execute(select(func.count()).select_from(users)).scalar()
        if has_users:
            return
        if not settings.should_seed_demo:
            # Empty production database: no demo accounts. The first org is
            # created via /api/auth/register (or a deliberate bootstrap).
            logger.info("init_db: empty database, demo seeding disabled — no accounts created")
            return
        if not settings.DEBUG and (
            settings.ADMIN_PASSWORD == "Admin@2024"
            or settings.MANAGER_PASSWORD == "Manager@2024"
        ):
            # They opted into seeding in production but left the built-in
            # passwords — the exact footgun this gate exists to prevent.
            logger.warning(
                "init_db: seeding demo accounts in a non-DEBUG environment with "
                "DEFAULT passwords. Rotate ceo/manager immediately or set "
                "SEED_DEMO_DATA=false."
            )
        _seed_demo_org(conn)


def _seed_demo_org(conn) -> None:
    from app.core.security import hash_password

    org_id = conn.execute(
        insert(organizations).values(name="Demo Organization", slug="demo")
    ).inserted_primary_key[0]
    conn.execute(insert(audit_chain_state).values(org_id=org_id, last_hash=GENESIS_HASH))
    conn.execute(
        insert(users),
        [
            {
                "org_id": org_id, "username": "ceo", "email": "ceo@demo.local",
                "password_hash": hash_password(settings.ADMIN_PASSWORD), "role": "owner",
            },
            {
                "org_id": org_id, "username": "manager", "email": "manager@demo.local",
                "password_hash": hash_password(settings.MANAGER_PASSWORD), "role": "member",
            },
        ],
    )


# ── Organizations + users ─────────────────────────────────────────────────────

def _slugify(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "org"
    return f"{base}-{secrets.token_hex(3)}"


def create_organization_with_owner(
    org_name: str, username: str, email: str, password_hash: str
) -> dict:
    """Self-service signup: create an org and its first (owner) user atomically.

    Raises ValueError('conflict') if the username or email already exists.
    """
    with get_engine().begin() as conn:
        try:
            org_id = conn.execute(
                insert(organizations).values(name=org_name.strip()[:120], slug=_slugify(org_name))
            ).inserted_primary_key[0]
            conn.execute(insert(audit_chain_state).values(org_id=org_id, last_hash=GENESIS_HASH))
            conn.execute(
                insert(users).values(
                    org_id=org_id, username=username, email=email,
                    password_hash=password_hash, role="owner",
                )
            )
        except IntegrityError:
            raise ValueError("conflict")
    return {"org_id": org_id, "username": username, "role": "owner"}


def create_user_in_org(
    org_id: int, username: str, email: str, password_hash: str, role: str
) -> None:
    if role not in {"admin", "member"}:
        raise ValueError("invalid_role")
    with get_engine().begin() as conn:
        try:
            conn.execute(
                insert(users).values(
                    org_id=org_id, username=username, email=email,
                    password_hash=password_hash, role=role,
                )
            )
        except IntegrityError:
            raise ValueError("conflict")


def list_org_users(org_id: int) -> list[dict]:
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(
                users.c.username, users.c.email, users.c.role,
                users.c.is_active, users.c.last_login, users.c.created_at,
            ).where(users.c.org_id == org_id).order_by(users.c.created_at)
        ).all()
    return [
        {
            "username": r.username, "email": r.email, "role": r.role,
            "is_active": bool(r.is_active),
            "last_login": r.last_login.isoformat() if r.last_login else None,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


def set_user_active(org_id: int, username: str, active: bool) -> bool:
    with get_engine().begin() as conn:
        result = conn.execute(
            update(users)
            .where(users.c.org_id == org_id, users.c.username == username)
            .values(is_active=active)
        )
        return result.rowcount > 0


def get_user_by_username(username: str) -> dict | None:
    with get_engine().connect() as conn:
        row = conn.execute(
            select(
                users.c.username, users.c.password_hash, users.c.role,
                users.c.is_active, users.c.failed_attempts, users.c.locked_until,
                users.c.org_id, users.c.email,
            ).where(users.c.username == username)
        ).first()
    if not row:
        return None
    return {
        "username": row.username,
        "password_hash": row.password_hash,
        "role": row.role,
        "is_active": bool(row.is_active),
        "failed_attempts": row.failed_attempts,
        "locked_until": row.locked_until,
        "org_id": row.org_id,
        "email": row.email,
    }


def record_failed_login(username: str) -> None:
    with get_engine().begin() as conn:
        conn.execute(
            update(users).where(users.c.username == username)
            .values(failed_attempts=users.c.failed_attempts + 1)
        )
        row = conn.execute(
            select(users.c.failed_attempts).where(users.c.username == username)
        ).first()
        if row and row.failed_attempts >= settings.MAX_LOGIN_ATTEMPTS:
            conn.execute(
                update(users).where(users.c.username == username)
                .values(locked_until=_now() + timedelta(minutes=settings.LOCKOUT_MINUTES))
            )


def record_successful_login(username: str) -> None:
    with get_engine().begin() as conn:
        conn.execute(
            update(users).where(users.c.username == username)
            .values(failed_attempts=0, locked_until=None, last_login=_now())
        )


# ── Refresh tokens ────────────────────────────────────────────────────────────

def store_refresh_token(jti: str, username: str, org_id: int, expires_at: datetime) -> None:
    with get_engine().begin() as conn:
        conn.execute(insert(refresh_tokens).values(
            jti=jti, username=username, org_id=org_id, expires_at=expires_at,
        ))


def is_refresh_token_valid(jti: str) -> bool:
    with get_engine().connect() as conn:
        row = conn.execute(
            select(refresh_tokens.c.revoked, refresh_tokens.c.expires_at)
            .where(refresh_tokens.c.jti == jti)
        ).first()
    if not row:
        return False
    expires_at = row.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return (not row.revoked) and expires_at > _now()


def revoke_refresh_token(jti: str) -> None:
    with get_engine().begin() as conn:
        conn.execute(update(refresh_tokens).where(refresh_tokens.c.jti == jti).values(revoked=True))


def revoke_all_user_tokens(username: str) -> None:
    with get_engine().begin() as conn:
        conn.execute(
            update(refresh_tokens).where(refresh_tokens.c.username == username).values(revoked=True)
        )


def purge_expired_refresh_tokens() -> None:
    with get_engine().begin() as conn:
        conn.execute(delete(refresh_tokens).where(refresh_tokens.c.expires_at < _now()))


# ── Session ownership (tenant-scoped) ─────────────────────────────────────────

def register_session(session_id: str, owner: str, org_id: int, table_name: str, row_count: int) -> None:
    with get_engine().begin() as conn:
        conn.execute(delete(sessions).where(sessions.c.session_id == session_id))
        conn.execute(insert(sessions).values(
            session_id=session_id, org_id=org_id, owner_username=owner,
            table_name=table_name, row_count=row_count,
        ))


def get_session(session_id: str) -> dict | None:
    with get_engine().connect() as conn:
        row = conn.execute(
            select(sessions.c.owner_username, sessions.c.org_id)
            .where(sessions.c.session_id == session_id)
        ).first()
    return {"owner": row.owner_username, "org_id": row.org_id} if row else None


def user_can_access_session(session_id: str, username: str, role: str, org_id: int) -> bool:
    sess = get_session(session_id)
    if sess is None:
        return False
    if sess["org_id"] != org_id:      # tenant isolation — never cross orgs
        return False
    return sess["owner"] == username or role in ADMIN_ROLES


# ── Audit log (tenant-scoped) ─────────────────────────────────────────────────

def write_audit_log(
    username: str, org_id: int, session_id: str, natural_query: str,
    generated_sql: str | None, result_summary: str, status: str,
) -> None:
    """Append a tamper-evident audit entry. The per-org chain-state row is locked
    (FOR UPDATE on Postgres; single-writer serialization on SQLite) so concurrent
    appends cannot fork the chain."""
    generated_sql = generated_sql or ""
    event_ts = _now().strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    with get_engine().begin() as conn:
        state = conn.execute(
            select(audit_chain_state.c.last_hash)
            .where(audit_chain_state.c.org_id == org_id)
            .with_for_update()
        ).first()
        prev_hash = state.last_hash if state else GENESIS_HASH

        entry_hash = _audit_entry_hash(
            prev_hash, org_id, username, session_id, natural_query,
            generated_sql, result_summary, status, event_ts,
        )
        conn.execute(insert(audit_logs).values(
            org_id=org_id, username=username, session_id=session_id,
            natural_query=natural_query, generated_sql=generated_sql,
            result_summary=result_summary, status=status,
            event_ts=event_ts, prev_hash=prev_hash, entry_hash=entry_hash,
        ))
        if state:
            conn.execute(
                update(audit_chain_state).where(audit_chain_state.c.org_id == org_id)
                .values(last_hash=entry_hash, updated_at=_now())
            )
        else:
            conn.execute(insert(audit_chain_state).values(org_id=org_id, last_hash=entry_hash))


def verify_audit_chain(org_id: int) -> dict:
    """Recompute the org's audit hash chain and report integrity."""
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(audit_logs).where(audit_logs.c.org_id == org_id).order_by(audit_logs.c.id.asc())
        ).all()
        head = conn.execute(
            select(audit_chain_state.c.last_hash).where(audit_chain_state.c.org_id == org_id)
        ).scalar()

    prev = GENESIS_HASH
    for r in rows:
        expected = _audit_entry_hash(
            prev, r.org_id, r.username, r.session_id, r.natural_query,
            r.generated_sql, r.result_summary, r.status, r.event_ts,
        )
        if r.prev_hash != prev or r.entry_hash != expected:
            return {"valid": False, "entries": len(rows), "broken_at": r.id}
        prev = r.entry_hash

    head_ok = (head is None and not rows) or (head == prev)
    return {"valid": head_ok, "entries": len(rows), "broken_at": None if head_ok else "head"}


def fetch_audit_logs(org_id: int, limit: int, offset: int) -> tuple[list[dict], int]:
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(audit_logs).where(audit_logs.c.org_id == org_id)
            .order_by(audit_logs.c.created_at.desc()).limit(limit).offset(offset)
        ).all()
        total = conn.execute(
            select(func.count()).select_from(audit_logs).where(audit_logs.c.org_id == org_id)
        ).scalar()
    items = [
        {
            "id": r.id, "username": r.username, "session_id": r.session_id,
            "question": r.natural_query, "sql": r.generated_sql,
            "summary": r.result_summary, "status": r.status,
            "timestamp": r.created_at.isoformat() if r.created_at else None,
            "entry_hash": r.entry_hash,
        }
        for r in rows
    ]
    return items, total


def fetch_session_logs(session_id: str, org_id: int) -> list[tuple]:
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(
                audit_logs.c.natural_query, audit_logs.c.generated_sql,
                audit_logs.c.result_summary, audit_logs.c.created_at,
            ).where(audit_logs.c.session_id == session_id, audit_logs.c.org_id == org_id)
            .order_by(audit_logs.c.created_at.asc())
        ).all()
    return [tuple(r) for r in rows]


# ── GDPR / data lifecycle ─────────────────────────────────────────────────────

def count_owners(org_id: int) -> int:
    with get_engine().connect() as conn:
        return conn.execute(
            select(func.count()).select_from(users)
            .where(users.c.org_id == org_id, users.c.role == "owner", users.c.is_active.is_(True))
        ).scalar()


def _org_session_ids(conn, org_id: int, owner: str | None = None) -> list[str]:
    stmt = select(sessions.c.session_id).where(sessions.c.org_id == org_id)
    if owner is not None:
        stmt = stmt.where(sessions.c.owner_username == owner)
    return [row[0] for row in conn.execute(stmt).all()]


def export_user_data(org_id: int, username: str) -> dict:
    """GDPR Article 15 — return everything the system holds about this user."""
    with get_engine().connect() as conn:
        profile = conn.execute(
            select(users.c.username, users.c.email, users.c.role,
                   users.c.is_active, users.c.last_login, users.c.created_at)
            .where(users.c.org_id == org_id, users.c.username == username)
        ).first()
        user_sessions = conn.execute(
            select(sessions.c.session_id, sessions.c.table_name,
                   sessions.c.row_count, sessions.c.created_at)
            .where(sessions.c.org_id == org_id, sessions.c.owner_username == username)
        ).all()
        activity = conn.execute(
            select(audit_logs.c.natural_query, audit_logs.c.generated_sql,
                   audit_logs.c.result_summary, audit_logs.c.status, audit_logs.c.created_at)
            .where(audit_logs.c.org_id == org_id, audit_logs.c.username == username)
            .order_by(audit_logs.c.id.asc())
        ).all()

    def _iso(v):
        return v.isoformat() if v else None

    return {
        "profile": {
            "username": profile.username, "email": profile.email, "role": profile.role,
            "is_active": bool(profile.is_active),
            "last_login": _iso(profile.last_login), "created_at": _iso(profile.created_at),
        } if profile else None,
        "datasets": [
            {"session_id": s.session_id, "table_name": s.table_name,
             "row_count": s.row_count, "created_at": _iso(s.created_at)}
            for s in user_sessions
        ],
        "activity": [
            {"question": a.natural_query, "sql": a.generated_sql, "summary": a.result_summary,
             "status": a.status, "timestamp": _iso(a.created_at)}
            for a in activity
        ],
    }


def delete_user_account(org_id: int, username: str) -> int:
    """GDPR Article 17 — erase the user's account and uploaded datasets.

    Audit-log rows are retained for security/legal-obligation reasons and to keep
    the tamper-evident chain intact; the username there is a pseudonymous handle.
    Returns the number of datasets deleted.
    """
    with get_engine().begin() as conn:
        session_ids = _org_session_ids(conn, org_id, owner=username)
    for sid in session_ids:
        delete_session_data(sid)  # removes DuckDB file + upload + sessions row
    with get_engine().begin() as conn:
        conn.execute(delete(refresh_tokens).where(refresh_tokens.c.username == username))
        conn.execute(delete(users).where(users.c.org_id == org_id, users.c.username == username))
    return len(session_ids)


def delete_organization(org_id: int) -> dict:
    """Erase an entire organization and all its data (owner-initiated)."""
    with get_engine().begin() as conn:
        session_ids = _org_session_ids(conn, org_id)
    for sid in session_ids:
        delete_session_data(sid)
    with get_engine().begin() as conn:
        users_deleted = conn.execute(
            select(func.count()).select_from(users).where(users.c.org_id == org_id)
        ).scalar()
        conn.execute(delete(audit_logs).where(audit_logs.c.org_id == org_id))
        conn.execute(delete(audit_chain_state).where(audit_chain_state.c.org_id == org_id))
        conn.execute(delete(refresh_tokens).where(refresh_tokens.c.org_id == org_id))
        conn.execute(delete(usage_counters).where(usage_counters.c.org_id == org_id))
        conn.execute(delete(users).where(users.c.org_id == org_id))
        conn.execute(delete(organizations).where(organizations.c.id == org_id))
    return {"datasets_deleted": len(session_ids), "users_deleted": users_deleted}


def delete_session_data(session_id: str) -> None:
    dataset_storage.delete(session_id)
    if settings.UPLOAD_DIR.exists():
        for f in settings.UPLOAD_DIR.glob(f"{session_id}.*"):
            f.unlink(missing_ok=True)
    with get_engine().begin() as conn:
        conn.execute(delete(sessions).where(sessions.c.session_id == session_id))


def cleanup_stale_sessions() -> int:
    cutoff = _now() - timedelta(hours=settings.SESSION_TTL_HOURS)
    with get_engine().connect() as conn:
        stale = conn.execute(
            select(sessions.c.session_id).where(sessions.c.created_at < cutoff)
        ).all()
    for (sid,) in stale:
        delete_session_data(sid)
    return len(stale)


# ── DuckDB (per-session data) ─────────────────────────────────────────────────

def get_user_duckdb(session_id: str) -> duckdb.DuckDBPyConnection:
    """Open a writable connection for a fresh upload.

    Call ``persist_user_duckdb`` after closing the connection so the S3 backend
    uploads the finished file; ``discard_user_duckdb`` on failure to clean up.
    """
    db_path = dataset_storage.path_for_write(session_id)
    return duckdb.connect(str(db_path))


def persist_user_duckdb(session_id: str) -> None:
    """Persist a just-written dataset to the durable store (no-op for local)."""
    dataset_storage.commit(session_id)


def discard_user_duckdb(session_id: str) -> None:
    """Remove a partially-written dataset from local cache and durable store."""
    dataset_storage.delete(session_id)


def require_user_duckdb(session_id: str) -> duckdb.DuckDBPyConnection:
    # Materialises from object storage on demand; raises FileNotFoundError if
    # the session does not exist.
    db_path = dataset_storage.ensure_local(session_id)
    conn = duckdb.connect(str(db_path))
    conn.execute("SET enable_external_access=false")
    return conn


# ── Per-tenant plan + usage metering ──────────────────────────────────────────

def get_org_plan(org_id: int) -> str:
    with get_engine().connect() as conn:
        plan = conn.execute(
            select(organizations.c.plan).where(organizations.c.id == org_id)
        ).scalar()
    return plan or "free"


def set_org_plan(org_id: int, plan: str) -> None:
    with get_engine().begin() as conn:
        conn.execute(
            update(organizations).where(organizations.c.id == org_id).values(plan=plan)
        )


def record_usage(org_id: int, metric: str, period: str, amount: int = 1) -> None:
    """Atomically add ``amount`` to an org's usage counter for the period.

    Uses an INSERT … ON CONFLICT DO UPDATE upsert (supported by both SQLite and
    Postgres) so concurrent increments across workers/replicas are safe.
    """
    engine = get_engine()
    if engine.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as _insert
    else:
        from sqlalchemy.dialects.sqlite import insert as _insert

    stmt = _insert(usage_counters).values(
        org_id=org_id, period=period, metric=metric, count=amount, updated_at=_now()
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["org_id", "period", "metric"],
        set_={"count": usage_counters.c.count + amount, "updated_at": _now()},
    )
    with engine.begin() as conn:
        conn.execute(stmt)


def get_usage(org_id: int, period: str) -> dict[str, int]:
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(usage_counters.c.metric, usage_counters.c.count).where(
                usage_counters.c.org_id == org_id,
                usage_counters.c.period == period,
            )
        ).all()
    return {metric: count for metric, count in rows}


# ── Stripe billing linkage ────────────────────────────────────────────────────

def get_org_name(org_id: int) -> str:
    with get_engine().connect() as conn:
        return conn.execute(
            select(organizations.c.name).where(organizations.c.id == org_id)
        ).scalar() or f"org-{org_id}"


def get_org_billing(org_id: int) -> dict:
    """Stripe linkage for an org. Empty strings mean "never subscribed"."""
    with get_engine().connect() as conn:
        row = conn.execute(
            select(
                organizations.c.plan,
                organizations.c.stripe_customer_id,
                organizations.c.stripe_subscription_id,
                organizations.c.billing_status,
            ).where(organizations.c.id == org_id)
        ).first()
    if row is None:
        return {"plan": "free", "customer_id": "", "subscription_id": "", "status": "none"}
    return {
        "plan": row[0] or "free",
        "customer_id": row[1] or "",
        "subscription_id": row[2] or "",
        "status": row[3] or "none",
    }


def set_org_billing(
    org_id: int,
    *,
    plan: str | None = None,
    customer_id: str | None = None,
    subscription_id: str | None = None,
    status: str | None = None,
) -> None:
    """Update whichever billing fields are supplied, leaving the rest alone."""
    values = {
        "plan": plan,
        "stripe_customer_id": customer_id,
        "stripe_subscription_id": subscription_id,
        "billing_status": status,
    }
    values = {k: v for k, v in values.items() if v is not None}
    if not values:
        return
    with get_engine().begin() as conn:
        conn.execute(update(organizations).where(organizations.c.id == org_id).values(**values))


def find_org_by_customer(customer_id: str) -> int | None:
    """Reverse-lookup an org from a Stripe customer id (used by webhooks)."""
    if not customer_id:
        return None
    with get_engine().connect() as conn:
        return conn.execute(
            select(organizations.c.id).where(
                organizations.c.stripe_customer_id == customer_id
            )
        ).scalar()


def claim_stripe_event(event_id: str, event_type: str = "") -> bool:
    """Record a webhook event id. Returns False if it was already processed.

    The primary-key insert is what makes this atomic — two concurrent replicas
    handling the same retried delivery cannot both win.
    """
    if not event_id:
        return True
    try:
        with get_engine().begin() as conn:
            conn.execute(
                insert(stripe_events).values(event_id=event_id, event_type=event_type)
            )
    except IntegrityError:
        return False
    return True
