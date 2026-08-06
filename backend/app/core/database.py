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
import hmac
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
    Float,
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
    # Issue #21. Defaults false so every *new* account starts unverified; the
    # migration grandfathers existing rows to true, because turning the gate on
    # must not lock out accounts that predate it.
    Column("email_verified", Boolean, nullable=False, default=False),
)

# Single-use email verification tokens (issue #21).
#
# Only the SHA-256 of the token is stored, for the same reason password hashes
# are: a leaked metadata DB otherwise hands over every pending account. The
# plaintext exists only in the mail.
email_verification_tokens = Table(
    "email_verification_tokens", metadata,
    Column("token_hash", String(64), primary_key=True),
    Column("username", String(50), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("used_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
    Index("ix_email_verification_username", "username"),
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
    # Running entry count — drives periodic checkpointing without a COUNT(*).
    Column("entries", BigInteger, nullable=False, default=0),
    Column("updated_at", DateTime(timezone=True), server_default=func.now()),
)

# Signed chain checkpoints (issue #30). Each row certifies "at audit_logs.id =
# last_id the chain head was last_hash", signed with the app's SECRET_KEY. That
# signature is what makes bounded verification meaningful: without it, verifying
# only recent entries would anchor on a hash an attacker with DB access could
# have rewritten. Written at append time, so the signed value is the head the
# process itself just computed — O(1), and correct by construction.
audit_checkpoints = Table(
    "audit_checkpoints", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("org_id", Integer, nullable=False),
    Column("last_id", Integer, nullable=False),        # audit_logs.id at checkpoint
    Column("last_hash", String(64), nullable=False),
    Column("entries", BigInteger, nullable=False, default=0),
    Column("signature", String(64), nullable=False),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
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

# Platform-wide calibration of "how many rows fit in an uploaded file" (issue
# #24). One row, id=1: a running aggregate over every successful upload, plus
# the *narrowest* file seen. Deliberately not per-org — this measures the shape
# of data, not a tenant's usage, and one org's uploads inform another's ceiling.
upload_shape_stats = Table(
    "upload_shape_stats", metadata,
    Column("id", Integer, primary_key=True),          # always 1
    Column("uploads", BigInteger, nullable=False, default=0),
    Column("total_bytes", BigInteger, nullable=False, default=0),
    Column("total_rows", BigInteger, nullable=False, default=0),
    # Smallest bytes-per-row ever observed — the worst case for a row ceiling,
    # because the narrower the rows the more of them fit in one upload.
    Column("min_bytes_per_row", Float, nullable=False, default=0.0),
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
Index("ix_audit_ckpt_org_last", audit_checkpoints.c.org_id, audit_checkpoints.c.last_id)

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


def _checkpoint_signature(org_id: int, last_id: int, last_hash: str, entries: int) -> str:
    """HMAC over a checkpoint, keyed on SECRET_KEY.

    A checkpoint is only useful as a verification anchor if it can't be forged
    by whoever can edit the rows it certifies. Anyone with DB access can rewrite
    audit_logs *and* recompute a consistent chain; what they cannot do without
    the app secret is produce this signature. So a bounded verify that starts
    from a signed checkpoint is still tamper-evident, while one that started
    from an unsigned row would not be.
    """
    payload = "\x1f".join([str(org_id), str(last_id), last_hash, str(entries)])
    return hmac.new(
        settings.SECRET_KEY.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()


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
            # Seeded accounts are verified: there is no mailbox behind
            # *@demo.local to receive a link, and the test fixtures log in as
            # these users (issue #21).
            {
                "org_id": org_id, "username": "ceo", "email": "ceo@demo.local",
                "password_hash": hash_password(settings.ADMIN_PASSWORD), "role": "owner",
                "email_verified": True,
            },
            {
                "org_id": org_id, "username": "manager", "email": "manager@demo.local",
                "password_hash": hash_password(settings.MANAGER_PASSWORD), "role": "member",
                "email_verified": True,
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
                    # An admin inside an existing org vouched for this address,
                    # and the org's own verification is what gates queries
                    # (issue #21) — so there is nothing for this user to prove.
                    email_verified=True,
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
                users.c.org_id, users.c.email, users.c.email_verified,
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
        "email_verified": bool(row.email_verified),
    }


# ── Email verification (issue #21) ────────────────────────────────────────────


def _hash_verification_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_email_verification_token(username: str, ttl_hours: int) -> str:
    """Mint a single-use verification token and return the *plaintext*.

    Any previously issued tokens for the user are dropped first, so a resend
    invalidates the older link rather than leaving several live at once.
    """
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(UTC) + timedelta(hours=ttl_hours)
    with get_engine().begin() as conn:
        conn.execute(
            delete(email_verification_tokens).where(
                email_verification_tokens.c.username == username
            )
        )
        conn.execute(
            insert(email_verification_tokens).values(
                token_hash=_hash_verification_token(token),
                username=username,
                expires_at=expires_at,
            )
        )
    return token


def consume_email_verification_token(token: str) -> str | None:
    """Verify a user from a token. Returns the username, or None if unusable.

    Single-use and time-bounded: an already-used or expired token returns None
    rather than silently re-verifying, so a leaked link from an old mail is
    inert. The whole thing runs in one transaction, so two racing requests can't
    both consume the same token.
    """
    now = datetime.now(UTC)
    with get_engine().begin() as conn:
        row = conn.execute(
            select(
                email_verification_tokens.c.username,
                email_verification_tokens.c.expires_at,
                email_verification_tokens.c.used_at,
            ).where(email_verification_tokens.c.token_hash == _hash_verification_token(token))
        ).first()
        if row is None or row.used_at is not None:
            return None

        expires_at = row.expires_at
        if expires_at.tzinfo is None:
            # SQLite hands back naive datetimes; compare in UTC either way.
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at <= now:
            return None

        conn.execute(
            update(email_verification_tokens)
            .where(
                email_verification_tokens.c.token_hash == _hash_verification_token(token)
            )
            .values(used_at=now)
        )
        conn.execute(
            update(users).where(users.c.username == row.username).values(email_verified=True)
        )
    return row.username


def is_email_verified(username: str) -> bool:
    with get_engine().connect() as conn:
        row = conn.execute(
            select(users.c.email_verified).where(users.c.username == username)
        ).first()
    # A missing user is not "verified" — the caller decides what to do about it.
    return bool(row.email_verified) if row else False


def is_org_email_verified(org_id: int) -> bool:
    """Whether an org's *owner* has confirmed their address (issue #21).

    The gate is per-org rather than per-user on purpose. Quota is an org-level
    budget and the abuse path is "register another org", so the org is the unit
    that has to be paid for once. Checking each user individually would also
    leave a hole: an unverified owner could create a member through the admin
    route and run queries as them.

    An org with no owner row is treated as unverified — that state shouldn't
    exist (signup creates the owner in the same transaction), and failing closed
    is the right side to err on for a gate.
    """
    with get_engine().connect() as conn:
        row = conn.execute(
            select(users.c.email_verified)
            .where(users.c.org_id == org_id, users.c.role == "owner")
            .order_by(users.c.id)
        ).first()
    return bool(row.email_verified) if row else False


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
            select(audit_chain_state.c.last_hash, audit_chain_state.c.entries)
            .where(audit_chain_state.c.org_id == org_id)
            .with_for_update()
        ).first()
        prev_hash = state.last_hash if state else GENESIS_HASH

        entry_hash = _audit_entry_hash(
            prev_hash, org_id, username, session_id, natural_query,
            generated_sql, result_summary, status, event_ts,
        )
        result = conn.execute(insert(audit_logs).values(
            org_id=org_id, username=username, session_id=session_id,
            natural_query=natural_query, generated_sql=generated_sql,
            result_summary=result_summary, status=status,
            event_ts=event_ts, prev_hash=prev_hash, entry_hash=entry_hash,
        ))
        entries = (state.entries if state else 0) + 1
        if state:
            conn.execute(
                update(audit_chain_state).where(audit_chain_state.c.org_id == org_id)
                .values(last_hash=entry_hash, entries=entries, updated_at=_now())
            )
        else:
            conn.execute(insert(audit_chain_state).values(
                org_id=org_id, last_hash=entry_hash, entries=entries
            ))

        # Periodic signed checkpoint so verification stays bounded (issue #30).
        # Inside the same transaction and the same lock as the append, so the
        # signed head can't race another writer.
        interval = settings.AUDIT_CHECKPOINT_INTERVAL
        if interval > 0 and entries % interval == 0:
            entry_id = result.inserted_primary_key[0]
            conn.execute(insert(audit_checkpoints).values(
                org_id=org_id, last_id=entry_id, last_hash=entry_hash,
                entries=entries,
                signature=_checkpoint_signature(org_id, entry_id, entry_hash, entries),
            ))


def latest_audit_checkpoint(org_id: int, at_or_before_id: int | None = None) -> dict | None:
    """Newest signed checkpoint for an org, optionally at or before an entry id.

    Returns ``None`` when there is no checkpoint, and marks ``signature_valid``
    False rather than hiding a forged one — a checkpoint that fails its HMAC is
    itself evidence of tampering and must not be used as an anchor.
    """
    stmt = select(audit_checkpoints).where(audit_checkpoints.c.org_id == org_id)
    if at_or_before_id is not None:
        stmt = stmt.where(audit_checkpoints.c.last_id <= at_or_before_id)
    with get_engine().connect() as conn:
        row = conn.execute(stmt.order_by(audit_checkpoints.c.last_id.desc()).limit(1)).first()
    if row is None:
        return None
    expected = _checkpoint_signature(row.org_id, row.last_id, row.last_hash, row.entries)
    return {
        "last_id": row.last_id,
        "last_hash": row.last_hash,
        "entries": int(row.entries),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "signature_valid": hmac.compare_digest(row.signature, expected),
    }


def verify_audit_chain(org_id: int, full: bool = True, since_id: int | None = None) -> dict:
    """Recompute an org's audit hash chain and report integrity.

    ``full=True`` (the default, and what a compliance answer needs) walks the
    whole chain from genesis: O(entries), which is what issue #30 is about.

    ``full=False`` verifies only the entries after the newest signed checkpoint,
    anchoring on the hash that checkpoint certifies. Cost is bounded by the
    checkpoint interval rather than by total history. The trade is stated in the
    response, not buried: ``scope``, ``verified_from_id`` and
    ``unverified_before_id`` say exactly how much of the chain the ``valid``
    flag covers, so an incremental pass can never be mistaken for a full audit.
    Tampering with entries older than the anchor is invisible to it — run a full
    verify (nightly / on demand) to catch that.

    ``since_id`` narrows further, to the newest checkpoint at or before that id.
    """
    anchor_hash = GENESIS_HASH
    anchor_id: int | None = None
    checkpoint: dict | None = None
    scope = "full"

    if not full:
        checkpoint = latest_audit_checkpoint(org_id, at_or_before_id=since_id)
        if checkpoint and not checkpoint["signature_valid"]:
            # A forged or edited checkpoint is a finding in its own right.
            return {
                "valid": False, "entries": 0, "broken_at": "checkpoint",
                "scope": "checkpoint", "verified_from_id": None,
                "unverified_before_id": None, "checkpoint": checkpoint,
                "detail": "Checkpoint signature does not verify — it was not "
                          "written by this application.",
            }
        if checkpoint:
            anchor_hash = checkpoint["last_hash"]
            anchor_id = checkpoint["last_id"]
            scope = "incremental"
        else:
            scope = "full"  # nothing to anchor on yet; a full walk is the honest answer

    stmt = select(audit_logs).where(audit_logs.c.org_id == org_id)
    if anchor_id is not None:
        stmt = stmt.where(audit_logs.c.id > anchor_id)
    with get_engine().connect() as conn:
        rows = conn.execute(stmt.order_by(audit_logs.c.id.asc())).all()
        head = conn.execute(
            select(audit_chain_state.c.last_hash).where(audit_chain_state.c.org_id == org_id)
        ).scalar()

    def _result(valid: bool, broken_at) -> dict:
        return {
            "valid": valid,
            "entries": len(rows),          # entries actually checked
            "broken_at": broken_at,
            "scope": scope,
            "verified_from_id": anchor_id,
            "unverified_before_id": anchor_id,
            "checkpoint": checkpoint,
        }

    prev = anchor_hash
    for r in rows:
        expected = _audit_entry_hash(
            prev, r.org_id, r.username, r.session_id, r.natural_query,
            r.generated_sql, r.result_summary, r.status, r.event_ts,
        )
        if r.prev_hash != prev or r.entry_hash != expected:
            return _result(False, r.id)
        prev = r.entry_hash

    # ``prev`` is the anchor when no rows were in scope, so this covers all three
    # cases: a chain with no state row yet, nothing new since the checkpoint (the
    # head must still be the anchor), and the normal "head is the last entry".
    head_ok = (head is None and not rows) or head == prev
    return _result(head_ok, None if head_ok else "head")


def write_audit_checkpoint(org_id: int) -> dict:
    """Sign the current chain head, after verifying everything since the last one.

    Refuses to certify a chain it can't verify — a checkpoint over corrupt
    history would launder the corruption into every later bounded verify.
    """
    verdict = verify_audit_chain(org_id, full=False)
    if not verdict["valid"]:
        return {"created": False, "reason": "chain does not verify", "verification": verdict}

    with get_engine().begin() as conn:
        state = conn.execute(
            select(audit_chain_state.c.last_hash, audit_chain_state.c.entries)
            .where(audit_chain_state.c.org_id == org_id)
            .with_for_update()
        ).first()
        if state is None:
            return {"created": False, "reason": "no audit entries yet"}
        last_id = conn.execute(
            select(func.max(audit_logs.c.id)).where(audit_logs.c.org_id == org_id)
        ).scalar()
        if last_id is None:
            return {"created": False, "reason": "no audit entries yet"}
        entries = int(state.entries)
        conn.execute(insert(audit_checkpoints).values(
            org_id=org_id, last_id=last_id, last_hash=state.last_hash, entries=entries,
            signature=_checkpoint_signature(org_id, last_id, state.last_hash, entries),
        ))
    return {
        "created": True, "last_id": last_id, "last_hash": state.last_hash, "entries": entries
    }


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
        conn.execute(delete(audit_checkpoints).where(audit_checkpoints.c.org_id == org_id))
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


# Uploads smaller than this are not folded into the bytes-per-row calibration.
# In a small file the header and per-file overhead dominate, so its bytes-per-row
# is inflated — and since the calibration keeps the *minimum*, letting small
# files in would bias the estimate high, which is the unsafe direction (it makes
# a max-size upload look like fewer rows than it is).
_MIN_ROWS_FOR_SHAPE = 1_000


def record_upload_shape(file_bytes: int, rows: int) -> None:
    """Fold one successful upload into the platform's bytes-per-row calibration.

    Best-effort and never on the request's critical path for correctness: a
    failure here loses a data point, not an upload.
    """
    if file_bytes <= 0 or rows < _MIN_ROWS_FOR_SHAPE:
        return
    bytes_per_row = file_bytes / rows
    engine = get_engine()
    try:
        with engine.begin() as conn:
            row = conn.execute(
                select(upload_shape_stats).where(upload_shape_stats.c.id == 1).with_for_update()
            ).first()
            if row is None:
                conn.execute(insert(upload_shape_stats).values(
                    id=1, uploads=1, total_bytes=file_bytes, total_rows=rows,
                    min_bytes_per_row=bytes_per_row, updated_at=_now(),
                ))
                return
            conn.execute(
                update(upload_shape_stats).where(upload_shape_stats.c.id == 1).values(
                    uploads=row.uploads + 1,
                    total_bytes=row.total_bytes + file_bytes,
                    total_rows=row.total_rows + rows,
                    min_bytes_per_row=min(row.min_bytes_per_row, bytes_per_row),
                    updated_at=_now(),
                )
            )
    except Exception:  # pragma: no cover - telemetry must never break an upload
        logger.warning("could not record upload shape", exc_info=True)


def get_upload_shape_stats() -> dict:
    """Observed upload shape: sample size, mean and narrowest bytes-per-row."""
    try:
        with get_engine().connect() as conn:
            row = conn.execute(
                select(upload_shape_stats).where(upload_shape_stats.c.id == 1)
            ).first()
    except Exception:  # pragma: no cover - pre-migration DB, or DB down
        row = None
    if row is None or not row.uploads or not row.total_rows:
        return {"uploads": 0, "mean_bytes_per_row": None, "min_bytes_per_row": None}
    return {
        "uploads": int(row.uploads),
        "mean_bytes_per_row": row.total_bytes / row.total_rows,
        "min_bytes_per_row": float(row.min_bytes_per_row),
    }


def reset_upload_shape_stats() -> None:
    """Discard the accumulated calibration.

    For operators whose ingestion has changed shape enough that old samples
    mislead (and for tests, which must not leak samples into each other).
    """
    with get_engine().begin() as conn:
        conn.execute(delete(upload_shape_stats))


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
