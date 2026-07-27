import duckdb
import pytest

from app.nl2sql.sql_validator import extract_sql, is_safe_sql, validate_and_fix_sql


@pytest.fixture
def conn():
    c = duckdb.connect()
    c.execute("CREATE TABLE sales (region TEXT, revenue INTEGER)")
    c.execute("INSERT INTO sales VALUES ('North', 100), ('South', 200)")
    c.execute("SET enable_external_access=false")
    return c


@pytest.mark.parametrize("sql", [
    "SELECT SUM(revenue) FROM sales",
    "WITH t AS (SELECT * FROM sales) SELECT region FROM t",
    "select region, revenue from sales where revenue > 50",
])
def test_valid_selects_pass(sql):
    assert is_safe_sql(sql) is True


@pytest.mark.parametrize("sql", [
    "DROP TABLE sales",
    "DELETE FROM sales",
    "UPDATE sales SET revenue = 0",
    "INSERT INTO sales VALUES ('X', 1)",
    "SELECT 1; DROP TABLE sales",              # stacked statement
    "ATTACH 'evil.db' AS e",
    "PRAGMA database_list",
    "COPY sales TO 'out.csv'",
])
def test_dangerous_sql_blocked(sql):
    assert is_safe_sql(sql) is False


@pytest.mark.parametrize("sql", [
    "SELECT * FROM read_csv('/etc/passwd')",
    "SELECT * FROM read_parquet('http://evil/x.parquet')",
    "SELECT * FROM glob('/**')",
    "SELECT * FROM read_json_auto('/etc/hosts')",
])
def test_file_reading_functions_blocked(sql):
    """The core exfiltration risk — SELECT that reads the filesystem/network."""
    assert is_safe_sql(sql) is False


def test_extract_from_code_fence():
    raw = "Here is the query:\n```sql\nSELECT 1\n```"
    assert extract_sql(raw) == "SELECT 1"


def test_validate_and_fix_rejects_file_read_even_if_syntactically_valid(conn):
    # read_csv is valid DuckDB syntax, but must be rejected by the safety layer.
    assert validate_and_fix_sql("SELECT * FROM read_csv('/etc/passwd')", conn) is None


def test_validate_and_fix_accepts_real_query(conn):
    assert validate_and_fix_sql("SELECT SUM(revenue) AS total FROM sales", conn) == \
        "SELECT SUM(revenue) AS total FROM sales"


def test_db_level_lockdown_blocks_file_read(conn):
    """Defense-in-depth: even if the validator were bypassed, the connection
    itself refuses external file access."""
    with pytest.raises(duckdb.Error):
        conn.execute("SELECT * FROM read_csv('/etc/passwd')").fetchall()


# ── REPLACE: a scalar function, not DDL ───────────────────────────────────────
# REPLACE was on the keyword denylist to stop CREATE OR REPLACE, but it is also
# a standard string function, so ordinary queries were rejected with "please
# rephrase". These pin both halves: the function is allowed, and every dangerous
# form stays blocked by a rule that never depended on that denylist entry.

@pytest.mark.parametrize("sql", [
    "SELECT REPLACE(city, 'St.', 'Saint') AS c FROM sales",
    "SELECT regexp_replace(name, 'a', 'b') FROM sales",
    "WITH x AS (SELECT REPLACE(city, ' ', '') AS c FROM sales) SELECT * FROM x",
])
def test_replace_as_a_string_function_is_allowed(sql):
    assert is_safe_sql(sql) is True


@pytest.mark.parametrize("sql,blocked_by", [
    ("CREATE OR REPLACE TABLE t AS SELECT 1", "the CREATE keyword"),
    ("INSERT OR REPLACE INTO t VALUES (1)", "the INSERT keyword"),
    ("REPLACE INTO t VALUES (1)", "not starting with SELECT/WITH"),
    ("SELECT 1; REPLACE INTO t VALUES (1)", "the stacked-statement ';' check"),
])
def test_dangerous_replace_forms_stay_blocked(sql, blocked_by):
    assert is_safe_sql(sql) is False, f"should be rejected by {blocked_by}"


def test_replace_query_survives_the_full_validate_path(conn):
    sql = "SELECT REPLACE(region, 'North', 'N') AS r FROM sales"
    assert validate_and_fix_sql(sql, conn) == sql
