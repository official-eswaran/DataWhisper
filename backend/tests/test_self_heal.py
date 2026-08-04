"""The self-heal path must actually run on a bind error.

Bind errors (e.g. `LIKE` on a DATE column) are the most common way LLM-generated
SQL fails. Before the fix, a query that failed to bind was caught by the
EXPLAIN gate and dead-ended on "please rephrase" — the heal machinery, wired
downstream of that gate, never got a chance to run. These tests pin the two
halves that were indistinguishable before: a bind failure now *routes into* the
heal, and a query that binds cleanly never triggers it.
"""
import duckdb
import pytest

from app.nl2sql import pipeline as pipeline_mod
from app.nl2sql.pipeline import NL2SQLPipeline, execute_with_healing
from app.nl2sql.sql_validator import validate_sql

BUGGY = "SELECT COUNT(*) FROM employees WHERE join_date LIKE '2021%'"
FIXED = "SELECT COUNT(*) AS n FROM employees WHERE YEAR(join_date) = 2021"


@pytest.fixture
def conn():
    c = duckdb.connect()
    c.execute("CREATE TABLE employees (emp_name TEXT, join_date DATE)")
    c.execute(
        "INSERT INTO employees VALUES "
        "('A', DATE '2021-03-15'), ('B', DATE '2020-07-22'), ('C', DATE '2019-11-10')"
    )
    return c


# ── validate_sql: distinguishes unsafe from safe-but-unbindable ────────────────

def test_validate_sql_reports_a_bind_error_instead_of_discarding_the_query(conn):
    sql, bind_error = validate_sql(BUGGY, conn)
    assert sql == BUGGY, "a safe query must be returned so it can reach the heal"
    assert bind_error is not None  # EXPLAIN caught the bind failure, not discarded


def test_validate_sql_passes_a_binding_query_with_no_error(conn):
    sql, bind_error = validate_sql(FIXED, conn)
    assert sql == FIXED
    assert bind_error is None


def test_validate_sql_still_rejects_unsafe_sql_outright(conn):
    sql, bind_error = validate_sql("DROP TABLE employees", conn)
    assert sql is None and bind_error is None


# ── execute_with_healing: the heal actually fires on a bind error ──────────────

def test_bind_error_routes_into_the_heal_and_the_repaired_query_runs(conn, monkeypatch):
    _, bind_error = validate_sql(BUGGY, conn)

    seen = {}
    def fake_llm(prompt):
        seen["prompt"] = prompt
        return FIXED
    monkeypatch.setattr(pipeline_mod, "call_local_llm", fake_llm)

    df = execute_with_healing(conn, BUGGY, "schema", initial_error=bind_error)

    assert "prompt" in seen, "the heal LLM was never called — the path is dead again"
    assert bind_error in seen["prompt"], "the model must see the actual bind error"
    assert int(df.iloc[0, 0]) == 1


def test_a_query_that_binds_never_triggers_the_heal(conn, monkeypatch):
    def fail(*_a, **_k):
        raise AssertionError("heal ran for a query that binds cleanly")
    monkeypatch.setattr(pipeline_mod, "call_local_llm", fail)

    df = execute_with_healing(conn, FIXED, "schema")  # initial_error defaults to None
    assert int(df.iloc[0, 0]) == 1


# ── pipeline.run: the wiring end to end ───────────────────────────────────────

def test_pipeline_recovers_the_emp_joined_2021_case(conn, monkeypatch):
    """The exact failure from the issue: the model first emits the LIKE query,
    the heal repairs it, and the user gets an answer instead of 'please rephrase'."""
    monkeypatch.setattr(pipeline_mod, "classify_intent", lambda q: "data_query")

    responses = iter([BUGGY, FIXED])  # first: generation, second: heal
    monkeypatch.setattr(pipeline_mod, "call_local_llm", lambda prompt: next(responses))

    result = NL2SQLPipeline(db_conn=conn).run("How many employees joined in 2021?")

    assert result["type"] != "error", result
    assert result["sql"] == BUGGY  # the SQL first attempted is what we report
    assert next(responses, "exhausted") == "exhausted"  # both calls were made


def test_pipeline_still_rephrases_when_the_query_is_unsafe(conn, monkeypatch):
    monkeypatch.setattr(pipeline_mod, "classify_intent", lambda q: "data_query")
    monkeypatch.setattr(pipeline_mod, "call_local_llm", lambda prompt: "DROP TABLE employees")

    result = NL2SQLPipeline(db_conn=conn).run("delete everything")
    assert result["type"] == "error"
    assert "rephrase" in result["message"].lower()
