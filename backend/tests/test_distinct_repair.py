"""DISTINCT repair for value-listing questions (issue #58).

Like the GROUP BY repair (#52) this reads DuckDB's serialized AST, an internal
format that can shift between versions — so a failure here should be loud rather
than the repair quietly becoming a no-op.

What makes this one different, and what most of these tests are about: the
trigger has to read the *question*. `SELECT region FROM sales_data` is the right
answer to "show me every order's region" and the wrong answer to "which regions
exist", and the SQL is identical. #52's trigger was pure AST and needed no such
judgement. So the guards are deliberately narrow in both directions, and the
tests spend most of their effort on what must NOT be rewritten.
"""
from __future__ import annotations

import duckdb
import pytest

from app.nl2sql.sql_repair import add_distinct_for_value_listing


@pytest.fixture
def conn():
    c = duckdb.connect(":memory:")
    c.execute(
        "CREATE TABLE sales_data("
        "region VARCHAR, category VARCHAR, product VARCHAR, "
        "quantity INTEGER, price INTEGER, total_amount INTEGER, order_date DATE)"
    )
    c.execute(
        "INSERT INTO sales_data VALUES "
        "('East','Electronics','Laptop',1,100,100,'2024-01-01'),"
        "('East','Furniture','Desk',2,200,400,'2024-02-01'),"
        "('West','Electronics','Mouse',3,50,150,'2024-03-01'),"
        "('West','Electronics','Mouse',1,50,50,'2024-04-01')"
    )
    return c


def repaired(conn, sql, question):
    return add_distinct_for_value_listing(sql, question, conn)


def is_distinct(sql: str) -> bool:
    return "DISTINCT" in sql.upper()


# ── It fires on the reported defect ───────────────────────────────────────────


@pytest.mark.parametrize(
    "question",
    [
        "List all the regions",
        "list the regions",
        "Show me the regions",
        "show all of the regions",
        "What regions are there?",
        "Give me the unique regions",
        "What are the distinct regions?",
        "Show the different regions",
    ],
)
def test_value_listing_questions_get_distinct(conn, question):
    out = repaired(conn, "SELECT region FROM sales_data", question)
    assert is_distinct(out), f"{question!r} did not trigger the repair: {out}"


def test_the_repair_actually_changes_the_answer(conn):
    """The point of #58: 4 rows of regions, not one per order."""
    before = conn.execute("SELECT region FROM sales_data").fetchall()
    out = repaired(conn, "SELECT region FROM sales_data", "List all the regions")
    after = conn.execute(out).fetchall()

    assert len(before) == 4
    assert len(after) == 2  # East, West
    assert {r[0] for r in after} == {"East", "West"}


def test_repaired_sql_still_runs(conn):
    out = repaired(conn, "SELECT product FROM sales_data", "List all the products")
    conn.execute(out).fetchall()  # must not raise


# ── It leaves everything else alone ───────────────────────────────────────────


def test_distribution_questions_are_not_deduplicated(conn):
    """Prompt rule 9's case. "Distribution of X" wants every raw value —
    deduplicating destroys the answer rather than tidying it."""
    sql = "SELECT total_amount FROM sales_data"
    assert repaired(conn, sql, "Show the distribution of total_amount") == sql
    assert repaired(conn, sql, "What is the spread of order amounts?") == sql
    assert repaired(conn, sql, "Show me the histogram of amounts") == sql


def test_counting_questions_are_untouched(conn):
    """`sales_product_variety` is the case the prompt-rule attempt broke: "how
    many different products" is COUNT(DISTINCT product) and needs no help. Two
    independent guards stop it — the "how many" cue and the FUNCTION projection."""
    sql = "SELECT COUNT(DISTINCT product) FROM sales_data"
    assert repaired(conn, sql, "How many different products are there?") == sql


def test_an_aggregate_projection_is_never_rewritten(conn):
    for sql in (
        "SELECT COUNT(*) FROM sales_data",
        "SELECT SUM(total_amount) FROM sales_data",
        "SELECT MAX(price) FROM sales_data",
    ):
        assert repaired(conn, sql, "List all the totals") == sql


def test_ranked_or_limited_queries_are_untouched(conn):
    """A ranking question is answering something else entirely; deduplicating it
    would change which row comes back."""
    for sql in (
        "SELECT product FROM sales_data ORDER BY price DESC",
        "SELECT product FROM sales_data ORDER BY price DESC LIMIT 1",
        "SELECT product FROM sales_data LIMIT 5",
    ):
        assert repaired(conn, sql, "Show me the products") == sql


def test_already_distinct_is_left_exactly_as_it_is(conn):
    sql = "SELECT DISTINCT region FROM sales_data"
    assert repaired(conn, sql, "List all the regions") == sql


def test_grouped_queries_are_untouched(conn):
    sql = "SELECT region, SUM(total_amount) FROM sales_data GROUP BY region"
    assert repaired(conn, sql, "List all the regions") == sql


def test_multi_column_projections_are_untouched(conn):
    """Two columns may be a deliberate pairing; deduplicating the combination is
    a different operation from listing one column's values."""
    sql = "SELECT region, product FROM sales_data"
    assert repaired(conn, sql, "List all the regions and products") == sql


def test_star_projection_is_untouched(conn):
    sql = "SELECT * FROM sales_data"
    assert repaired(conn, sql, "Show me all the rows") == sql


def test_joins_and_subqueries_are_untouched(conn):
    """"The set of values" gets ambiguous once more than one table is involved."""
    sql = "SELECT region FROM (SELECT region FROM sales_data) t"
    assert repaired(conn, sql, "List all the regions") == sql


def test_per_row_questions_are_untouched(conn):
    """"each"/"per" signal one row per record, which is the opposite of a set."""
    sql = "SELECT region FROM sales_data"
    assert repaired(conn, sql, "Show the region for each order") == sql
    assert repaired(conn, sql, "Show revenue per region") == sql


def test_questions_with_no_listing_cue_are_untouched(conn):
    sql = "SELECT region FROM sales_data"
    assert repaired(conn, sql, "Tell me about regions") == sql
    assert repaired(conn, sql, "regions") == sql


def test_a_where_clause_survives_the_repair(conn):
    """Filtering then listing is still listing — the filter must be preserved."""
    out = repaired(
        conn,
        "SELECT product FROM sales_data WHERE region = 'West'",
        "List all the products",
    )
    assert is_distinct(out)
    assert conn.execute(out).fetchall() == [("Mouse",)]


# ── Robustness ────────────────────────────────────────────────────────────────


def test_unparseable_sql_is_returned_unchanged(conn):
    sql = "SELECT region FROM"  # syntactically invalid
    assert repaired(conn, sql, "List all the regions") == sql


def test_a_broken_connection_does_not_raise(conn):
    """The repair is best-effort; it must never turn a working query into a 500."""

    class Exploding:
        def execute(self, *a, **k):
            raise RuntimeError("boom")

    sql = "SELECT region FROM sales_data"
    assert add_distinct_for_value_listing(sql, "List all the regions", Exploding()) == sql


def test_non_select_statements_are_untouched(conn):
    sql = "SELECT region FROM sales_data UNION SELECT product FROM sales_data"
    assert repaired(conn, sql, "List all the regions") == sql
