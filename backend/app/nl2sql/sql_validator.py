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
#
# REPLACE is deliberately NOT here. It is a standard scalar string function
# (``SELECT REPLACE(city, 'St.', 'Saint')``), and blocking the bare word
# rejected ordinary queries with an unhelpful "please rephrase". Every dangerous
# form of the word is already caught by an independent rule that does not depend
# on this list:
#   CREATE OR REPLACE …   -> CREATE below
#   INSERT OR REPLACE …   -> INSERT below
#   REPLACE INTO …        -> does not start with SELECT/WITH (_SELECT_START_RE)
#   SELECT 1; REPLACE …   -> the ';' rejection in is_safe_sql
# so removing it costs no coverage. See test_sql_validator.py, which pins all
# four of those.
_FORBIDDEN_KEYWORDS = [
    "DROP", "DELETE", "UPDATE", "INSERT", "ALTER", "CREATE", "TRUNCATE",
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


def validate_sql(llm_response: str, conn) -> tuple[str | None, str | None]:
    """Extract, safety-check, and bind-check an LLM SQL response.

    Returns ``(sql, bind_error)``:

    * ``(None, None)``  — unsafe or unparseable. Unrecoverable; do not heal.
    * ``(sql, None)``   — safe and binds cleanly; ready to execute.
    * ``(sql, error)``  — safe but ``EXPLAIN`` failed to bind it. The caller
      should route this to the self-heal path, feeding ``error`` back to the
      model, rather than dead-ending on "please rephrase". Bind errors are the
      most common LLM SQL failure and are exactly what the heal prompt fixes.

    The safety gate (``is_safe_sql``) is authoritative: a query that fails it
    never leaves this function, so nothing downstream can execute unsafe SQL.
    """
    sql = extract_sql(llm_response)
    if not is_safe_sql(sql):
        return None, None
    try:
        conn.execute(f"EXPLAIN {sql}")
    except Exception as exc:
        return sql, str(exc)
    return sql, None


def validate_and_fix_sql(llm_response: str, conn) -> str | None:
    """Extract, validate, and syntax-check. Returns safe SQL or None.

    Bind failures return None here — use this where a failure is terminal, such
    as re-validating an already-healed query (there is no second heal). New call
    sites that want to route bind errors into the heal path should use
    ``validate_sql`` instead.
    """
    sql, bind_error = validate_sql(llm_response, conn)
    return sql if bind_error is None else None
