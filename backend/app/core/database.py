import sqlite3
import duckdb
from pathlib import Path

from app.core.config import settings

AUDIT_DB_PATH = settings.DATABASE_DIR / "audit.db"


# ── Audit + Users (SQLite) ────────────────────────────────────────────────────

def init_audit_db():
    """Create audit_logs and users tables, seed default accounts on first run."""
    settings.DATABASE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(AUDIT_DB_PATH))

    conn.execute("""
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL DEFAULT '',
            session_id TEXT,
            natural_query TEXT NOT NULL,
            generated_sql TEXT,
            result_summary TEXT,
            status TEXT DEFAULT 'success',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ── Schema migrations (safe to run repeatedly) ────────────────────────────
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(audit_logs)").fetchall()}
    if "username" not in existing_cols:
        conn.execute("ALTER TABLE audit_logs ADD COLUMN username TEXT NOT NULL DEFAULT ''")
    if "session_id" not in existing_cols:
        conn.execute("ALTER TABLE audit_logs ADD COLUMN session_id TEXT")
    # Rename legacy user_id → username by copying data (SQLite can't rename cols in old versions)
    if "user_id" in existing_cols and "username" in existing_cols:
        conn.execute("UPDATE audit_logs SET username = user_id WHERE username = ''")
    # ──────────────────────────────────────────────────────────────────────────

    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'department',
            is_active INTEGER NOT NULL DEFAULT 1,
            failed_attempts INTEGER NOT NULL DEFAULT 0,
            locked_until TIMESTAMP,
            last_login TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()

    # Seed default users only if the table is empty
    count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    if count == 0:
        _seed_default_users(conn)

    conn.close()


def _seed_default_users(conn: sqlite3.Connection):
    """Insert hashed default accounts — import here to avoid circular deps."""
    from app.core.security import hash_password  # local import avoids circular

    users = [
        ("ceo",     hash_password(settings.ADMIN_PASSWORD),   "admin"),
        ("manager", hash_password(settings.MANAGER_PASSWORD), "department"),
    ]
    conn.executemany(
        "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
        users,
    )
    conn.commit()


def get_audit_db() -> sqlite3.Connection:
    return sqlite3.connect(str(AUDIT_DB_PATH))


# ── User helpers ──────────────────────────────────────────────────────────────

def get_user_by_username(username: str) -> dict | None:
    conn = get_audit_db()
    row = conn.execute(
        "SELECT username, password_hash, role, is_active, failed_attempts, locked_until "
        "FROM users WHERE username = ?",
        (username,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    return {
        "username":       row[0],
        "password_hash":  row[1],
        "role":           row[2],
        "is_active":      bool(row[3]),
        "failed_attempts": row[4],
        "locked_until":   row[5],
    }


def record_failed_login(username: str):
    conn = get_audit_db()
    conn.execute(
        "UPDATE users SET failed_attempts = failed_attempts + 1 WHERE username = ?",
        (username,),
    )
    # Lock the account if threshold reached
    conn.execute(
        """UPDATE users
           SET locked_until = datetime('now', ? || ' minutes')
           WHERE username = ? AND failed_attempts >= ?""",
        (str(settings.LOCKOUT_MINUTES), username, settings.MAX_LOGIN_ATTEMPTS),
    )
    conn.commit()
    conn.close()


def record_successful_login(username: str):
    conn = get_audit_db()
    conn.execute(
        "UPDATE users SET failed_attempts = 0, locked_until = NULL, "
        "last_login = CURRENT_TIMESTAMP WHERE username = ?",
        (username,),
    )
    conn.commit()
    conn.close()


# ── DuckDB (per-session data) ─────────────────────────────────────────────────

def get_user_duckdb(session_id: str):
    """Create or connect to a user's DuckDB instance (used during upload)."""
    settings.DATABASE_DIR.mkdir(parents=True, exist_ok=True)
    db_path = settings.DATABASE_DIR / f"{session_id}.duckdb"
    return duckdb.connect(str(db_path))


def require_user_duckdb(session_id: str):
    """Connect to an existing user DuckDB — raises 404-friendly error if session missing."""
    db_path = settings.DATABASE_DIR / f"{session_id}.duckdb"
    if not db_path.exists():
        raise FileNotFoundError(f"Session '{session_id}' not found. Please upload data first.")
    return duckdb.connect(str(db_path))
