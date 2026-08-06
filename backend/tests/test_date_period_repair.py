"""Date period bounds repair (issue #69).

Like the other two repairs this reads DuckDB's serialized AST — an internal
format that can shift between versions — so a failure here should be loud rather
than the repair quietly becoming a no-op.

The defect: a period named in the question ("in March 2024", "in 2021",
"before 2020") becomes a single boundary instead of a range, so the query
returns a plausible number for the wrong window. One day's revenue reads as a
bad month, not as a bug.

Most of the effort below is on what must *not* be rewritten. The trigger reads
the question, so the risk is firing on a query that was already right — and two
eval cases (`sales_q1_orders`, `sales_h2_orders`) pass 3/3 today and phrase
their ranges explicitly.
"""
from __future__ import annotations

import duckdb
import pytest

from app.nl2sql.sql_repair import repair_date_period_bounds


@pytest.fixture
def conn():
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE sales_data(order_date DATE, region VARCHAR, total_amount INTEGER)")
    c.execute(
        "INSERT INTO sales_data VALUES "
        "('2024-03-01','East',100),('2024-03-15','East',200),('2024-03-31','West',300),"
        "('2024-04-01','East',999),('2024-02-28','West',50)"
    )
    c.execute("CREATE TABLE employees(emp_name VARCHAR, join_date DATE)")
    c.execute(
        "INSERT INTO employees VALUES "
        "('ana','2019-05-01'),('bo','2020-06-01'),('cy','2021-02-01'),"
        "('di','2021-11-30'),('ed','2022-01-01')"
    )
    return c


def fix(conn, sql, question):
    return repair_date_period_bounds(sql, question, conn)


# ── The three reported failures ───────────────────────────────────────────────


def test_month_period_becomes_a_range(conn):
    """sales_march_revenue — 0/3. One day's revenue answered "in March"."""
    sql = "SELECT SUM(total_amount) FROM sales_data WHERE order_date = '2024-03-01'"
    out = fix(conn, sql, "What was the total revenue in March 2024?")

    assert conn.execute(sql).fetchone()[0] == 100          # the bug
    assert conn.execute(out).fetchone()[0] == 600          # 100+200+300, April excluded


def test_year_period_becomes_a_range(conn):
    """emp_joined_2021 — 1/3."""
    sql = "SELECT COUNT(*) FROM employees WHERE join_date = '2021-01-01'"
    out = fix(conn, sql, "How many employees joined in 2021?")

    assert conn.execute(sql).fetchone()[0] == 0
    assert conn.execute(out).fetchone()[0] == 2            # cy, di — not ed (2022)


def test_before_a_year_excludes_that_whole_year(conn):
    """emp_joined_before_2020 — 0/3. `< '2020-12-31'` admits all of 2020, which
    is exactly who "before 2020" excludes."""
    sql = "SELECT emp_name FROM employees WHERE join_date < '2020-12-31'"
    out = fix(conn, sql, "Which employees joined before 2020?")

    assert [r[0] for r in conn.execute(sql).fetchall()] == ["ana", "bo"]
    assert [r[0] for r in conn.execute(out).fetchall()] == ["ana"]


def test_after_a_year_starts_at_the_next_one(conn):
    sql = "SELECT emp_name FROM employees WHERE join_date > '2021-06-01'"
    out = fix(conn, sql, "Which employees joined after 2021?")
    assert [r[0] for r in conn.execute(out).fetchall()] == ["ed"]


# ── Must not fire ─────────────────────────────────────────────────────────────


def test_an_already_correct_range_is_untouched(conn):
    sql = (
        "SELECT SUM(total_amount) FROM sales_data "
        "WHERE order_date >= '2024-03-01' AND order_date < '2024-04-01'"
    )
    assert fix(conn, sql, "What was the total revenue in March 2024?") == sql


def test_an_already_correct_before_bound_is_untouched(conn):
    sql = "SELECT emp_name FROM employees WHERE join_date < '2020-01-01'"
    assert fix(conn, sql, "Which employees joined before 2020?") == sql


def test_quarter_and_half_phrasings_are_not_matched(conn):
    """`sales_q1_orders` and `sales_h2_orders` pass 3/3 and are what a careless
    trigger would break — "of 2024" must not read as a year period.

    Deliberately uses a *single* equality comparison. An already-correct
    two-sided range would be protected by the "exactly one candidate" guard no
    matter what the question regex did, which made the first version of this
    test vacuous: a mutation widening the regex to match "of 2024" left it
    green. Here only the regex can prevent the rewrite — and rewriting would
    widen a quarter into a whole year.
    """
    sql = "SELECT COUNT(*) FROM sales_data WHERE order_date = '2024-01-01'"
    assert fix(conn, sql, "How many orders were placed in the first quarter of 2024?") == sql
    assert fix(conn, sql, "How many orders were placed in the second half of 2024?") == sql
    assert fix(conn, sql, "How many orders were placed in the last month of 2024?") == sql


def test_a_question_naming_no_period_is_untouched(conn):
    sql = "SELECT SUM(total_amount) FROM sales_data WHERE order_date = '2024-03-01'"
    assert fix(conn, sql, "What was the revenue on 2024-03-01?") == sql
    assert fix(conn, sql, "Show me the revenue") == sql


def test_two_periods_are_not_guessed_at(conn):
    """"2020 compared to 2021" has no single right answer here."""
    sql = "SELECT COUNT(*) FROM employees WHERE join_date = '2021-01-01'"
    assert fix(conn, sql, "How many joined in 2020 compared to in 2021?") == sql


def test_a_literal_outside_the_named_period_is_untouched(conn):
    """Question and SQL are talking about different things; not this defect."""
    sql = "SELECT COUNT(*) FROM employees WHERE join_date = '2019-01-01'"
    assert fix(conn, sql, "How many employees joined in 2021?") == sql


def test_a_second_date_comparison_stops_the_repair(conn):
    """Two date comparisons mean the model already built some range. Rewriting
    one half of it would produce something neither party asked for.

    Both comparisons are equalities inside the period, so both are candidates
    the repair could act on. That matters: the first version of this test used
    `>= … AND <= …`, where the leading operator is not `=` and the "within
    requires COMPARE_EQUAL" branch already declined — so the count guard was
    never exercised and a mutation removing it left the test green.
    """
    sql = (
        "SELECT COUNT(*) FROM employees "
        "WHERE join_date = '2021-01-01' OR join_date = '2021-02-01'"
    )
    assert fix(conn, sql, "How many employees joined in 2021?") == sql


def test_an_existing_two_sided_range_is_also_left_alone(conn):
    """The shape the previous version of the test above was reaching for."""
    sql = (
        "SELECT COUNT(*) FROM employees "
        "WHERE join_date >= '2021-01-01' AND join_date <= '2021-06-30'"
    )
    assert fix(conn, sql, "How many employees joined in 2021?") == sql


def test_a_non_date_filter_alongside_is_fine(conn):
    """One date comparison plus an unrelated predicate still repairs, and the
    other predicate survives."""
    sql = (
        "SELECT SUM(total_amount) FROM sales_data "
        "WHERE order_date = '2024-03-01' AND region = 'East'"
    )
    out = fix(conn, sql, "What was the total revenue in March 2024?")
    assert conn.execute(out).fetchone()[0] == 300          # East only: 100+200
    assert "region" in out


def test_the_wrong_operator_for_the_period_kind_is_untouched(conn):
    """"in 2021" pairs with `=`; a `<` there is a different query and not
    obviously the reported defect."""
    sql = "SELECT COUNT(*) FROM employees WHERE join_date < '2021-06-01'"
    assert fix(conn, sql, "How many employees joined in 2021?") == sql


def test_a_query_with_no_where_clause_is_untouched(conn):
    sql = "SELECT COUNT(*) FROM employees"
    assert fix(conn, sql, "How many employees joined in 2021?") == sql


# ── Boundaries and robustness ─────────────────────────────────────────────────


def test_december_rolls_into_the_next_year(conn):
    sql = "SELECT COUNT(*) FROM employees WHERE join_date = '2021-12-01'"
    out = fix(conn, sql, "How many employees joined in December 2021?")
    assert "2022-01-01" in out


def test_the_range_is_half_open_not_inclusive(conn):
    """An inclusive upper bound would pull in the first row of the next month —
    the 2024-04-01 row here, worth 999."""
    sql = "SELECT SUM(total_amount) FROM sales_data WHERE order_date = '2024-03-01'"
    out = fix(conn, sql, "What was the total revenue in March 2024?")
    assert conn.execute(out).fetchone()[0] == 600
    assert "<" in out and "<=" not in out


def test_unparseable_sql_is_returned_unchanged(conn):
    sql = "SELECT SUM(total_amount) FROM"
    assert fix(conn, sql, "What was the total revenue in March 2024?") == sql


def test_a_broken_connection_does_not_raise(conn):
    class Exploding:
        def execute(self, *a, **k):
            raise RuntimeError("boom")

    sql = "SELECT COUNT(*) FROM employees WHERE join_date = '2021-01-01'"
    assert repair_date_period_bounds(sql, "How many joined in 2021?", Exploding()) == sql


def test_repaired_sql_stays_safe(conn):
    """The rewrite goes back through is_safe_sql, keeping the safety gate
    authoritative even though the AST came from DuckDB's own serializer."""
    from app.nl2sql.sql_validator import is_safe_sql

    out = fix(
        conn,
        "SELECT SUM(total_amount) FROM sales_data WHERE order_date = '2024-03-01'",
        "What was the total revenue in March 2024?",
    )
    assert is_safe_sql(out)
