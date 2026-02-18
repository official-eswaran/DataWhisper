import sqlite3
import duckdb
from pathlib import Path

from app.core.config import settings

AUDIT_DB_PATH = settings.DATABASE_DIR / "audit.db"


def init_audit_db():
    """Initialize the audit log database."""
    settings.DATABASE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(AUDIT_DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            natural_query TEXT NOT NULL,
            generated_sql TEXT,
            result_summary TEXT,
            status TEXT DEFAULT 'success',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


def get_audit_db():
    return sqlite3.connect(str(AUDIT_DB_PATH))


def get_user_duckdb(session_id: str):
    """Each upload session gets its own DuckDB instance — full isolation."""
    db_path = settings.DATABASE_DIR / f"{session_id}.duckdb"
    return duckdb.connect(str(db_path))
