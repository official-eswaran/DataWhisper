"""SQL safety gate for LLM-generated queries.

Defense in depth. The query-time DuckDB connection already runs with
``enable_external_access=false`` (see ``require_user_duckdb``), which physically
blocks file/URL access. This module adds a second, independent layer:

1. Only a single statement is allowed (no stacked queries).
2. The statement must begin with SELECT or WITH.
3. A denylist blocks DDL/DML and file-reading table functions outright.

A denylist alone is brittle, which is why the DB-level lockdown is the primary
control; this validator is the belt to that connection's suspenders.
"""
from __future__ import annotations

import re

# Write / DDL / admin operations — must never appear.
_FORBIDDEN_KEYWORDS = [
    "DROP", "DELETE", "UPDATE", "INSERT", "ALTER", "CREATE", "TRUNCATE", "REPLACE",
    "GRANT", "REVOKE", "MERGE",
    # DuckDB write/exec/attach operations.
    "COPY", "EXPORT", "IMPORT", "ATTACH", "DETACH", "INSTALL", "LOAD",
    "PRAGMA", "VACUUM", "CHECKPOINT", "SET", "RESET", "CALL",
]

# File / network reading table functions — the real exfiltration risk.
_FORBIDDEN_FUNCTIONS = [
    "read_csv", "read_csv_auto", "read_parquet", "read_json", "read_json_auto",
    "read_ndjson", "read_ndjson_auto", "read_text", "read_blob",
    "parquet_scan", "csv_scan", "glob", "sniff_csv",
    "delta_scan", "iceberg_scan", "postgres_scan", "sqlite_scan", "mysql_scan",
]

_SELECT_START_RE = re.compile(r"^\s*(SELECT|WITH)\b", re.IGNORECASE)


def extract_sql(llm_response: str) -> str:
    """Pull a SQL statement out of a raw LLM response."""
    match = re.search(r"```(?:sql)?\s*(.*?)```", llm_response, re.DOTALL)
    if match:
        return match.group(1).strip().rstrip(";").strip()
    match = re.search(r"((?:WITH|SELECT)\s.+)", llm_response, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip().rstrip(";").strip()
    return llm_response.strip().rstrip(";").strip()


def is_safe_sql(sql: str) -> bool:
    stripped = sql.strip()
    if not stripped:
        return False

    # Reject stacked statements: only one trailing semicolon is tolerated and
    # extract_sql already stripped it, so any remaining ';' is suspicious.
    if ";" in stripped:
        return False

    if not _SELECT_START_RE.match(stripped):
        return False

    upper = stripped.upper()
    for kw in _FORBIDDEN_KEYWORDS:
        if re.search(rf"\b{kw}\b", upper):
            return False

    lower = stripped.lower()
    for fn in _FORBIDDEN_FUNCTIONS:
        # Match the function name only when it is actually being called.
        if re.search(rf"\b{re.escape(fn)}\s*\(", lower):
            return False

    return True


def validate_and_fix_sql(llm_response: str, conn) -> str | None:
    """Extract, validate, and syntax-check. Returns safe SQL or None."""
    sql = extract_sql(llm_response)
    if not is_safe_sql(sql):
        return None
    try:
        conn.execute(f"EXPLAIN {sql}")
        return sql
    except Exception:
        return None
