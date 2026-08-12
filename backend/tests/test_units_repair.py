"""Units-vs-rows repair (issue #60).

    "How many laptops were sold?"
      -> SELECT COUNT(CASE WHEN product = 'Laptop' THEN quantity END) FROM sales_data

That returns 3 — laptop *orders*. The answer is 11 units. It is the worst
failure shape this product has: a plausible number, in range, with nothing about
it that reads as wrong.

"How many laptops" genuinely admits both readings, and the repair does not pick
one. It fires only where the model already reached for the quantity column and
then threw its values away, which `COUNT(*)` — the way to count rows, and what
"How many orders are there?" produces 3/3 — never does.

Most of the tests below are therefore about what must *not* be rewritten.
"""
from __future__ import annotations

import duckdb
import pytest

from app.nl2sql.sql_repair import sum_the_measure_a_count_discarded

QUESTION = "How many laptops were sold?"


@pytest.fixture
def conn():
    """Three laptop orders carrying 11 units between them — the gap the defect
    hides. `order_id` is unique, `quantity` repeats: that is the difference
    between a column that identifies a row and one that measures it.
    """
    c = duckdb.connect(":memory:")
    c.execute(
        "CREATE TABLE sales_data("
        "order_id INTEGER, region VARCHAR, category VARCHAR, product VARCHAR, "
        "quantity INTEGER, total_amount INTEGER)"
    )
    c.execute(
        "INSERT INTO sales_data VALUES "
        "(1,'East','Electronics','Laptop',2,3000),"
        "(2,'West','Electronics','Laptop',5,7500),"
        "(3,'East','Electronics','Laptop',4,6000),"
        "(4,'West','Electronics','Mouse',2,100),"
        "(5,'East','Furniture','Desk',2,600)"
    )
    return c


def fix(conn, sql, question=QUESTION):
    return sum_the_measure_a_count_discarded(sql, question, conn)


# ── The reported failure ──────────────────────────────────────────────────────


def test_the_conditional_count_becomes_a_sum(conn):
    """sales_laptop_units — 0/3, and the form the model emits most often."""
    sql = "SELECT COUNT(CASE WHEN product = 'Laptop' THEN quantity END) FROM sales_data"
    out = fix(conn, sql)

    assert conn.execute(sql).fetchall() == [(3,)]     # laptop orders
    assert conn.execute(out).fetchall() == [(11,)]    # laptop units


def test_the_missing_aggregate_form_is_repaired_too(conn):
    """The other observed output — the raw column for every matching row, where
    one number was asked for."""
    sql = "SELECT quantity FROM sales_data WHERE product = 'Laptop'"
    out = fix(conn, sql)

    assert conn.execute(sql).fetchall() == [(2,), (5,), (4,)]
    assert conn.execute(out).fetchall() == [(11,)]


def test_a_plain_count_of_the_measure_is_summed(conn):
    """`COUNT(quantity) … WHERE product = 'Laptop'` discards the quantities in
    exactly the same way, without the CASE."""
    sql = "SELECT COUNT(quantity) FROM sales_data WHERE product = 'Laptop'"
    assert conn.execute(fix(conn, sql)).fetchall() == [(11,)]


def test_the_where_clause_survives(conn):
    sql = "SELECT quantity FROM sales_data WHERE product = 'Laptop'"
    assert "WHERE" in fix(conn, sql).upper()


def test_the_literal_may_sit_on_either_side_of_the_comparison(conn):
    sql = "SELECT COUNT(quantity) FROM sales_data WHERE 'Laptop' = product"
    assert conn.execute(fix(conn, sql)).fetchall() == [(11,)]


def test_matching_the_value_ignores_case(conn):
    """The question capitalises where the data does not, or the reverse."""
    sql = "SELECT COUNT(quantity) FROM sales_data WHERE product = 'Laptop'"
    out = fix(conn, sql, "How many LAPTOPS were sold?")
    assert conn.execute(out).fetchall() == [(11,)]


# ── What must not be rewritten ────────────────────────────────────────────────


def test_a_row_count_is_left_alone(conn):
    """sales_order_count — "How many orders are there?", 3/3 today. `COUNT(*)`
    holds no measure, so there is nothing for this repair to reach."""
    sql = "SELECT COUNT(*) FROM sales_data"
    assert fix(conn, sql, "How many orders are there?") == sql


def test_a_conditional_row_count_is_left_alone(conn):
    """`THEN 1` is how you count rows conditionally, and it means the model
    chose rows. The repair must not overrule that choice."""
    sql = "SELECT COUNT(CASE WHEN product = 'Laptop' THEN 1 END) FROM sales_data"
    assert fix(conn, sql) == sql


def test_a_filtered_row_count_is_left_alone(conn):
    sql = "SELECT COUNT(*) FROM sales_data WHERE product = 'Laptop'"
    assert fix(conn, sql) == sql


def test_a_question_naming_a_head_noun_after_the_value_declines(conn):
    """"How many Electronics **orders**" counts rows, and says so — the noun
    after "how many" is a modifier, not the thing being counted."""
    sql = "SELECT COUNT(quantity) FROM sales_data WHERE category = 'Electronics'"
    assert fix(conn, sql, "How many Electronics orders were placed?") == sql


def test_a_question_whose_noun_is_not_in_the_filter_declines(conn):
    """sales_total_quantity — "How many units were sold in total?", 3/3 today.

    Deliberate limit: without a filter naming what is being counted there is no
    confirmation that the question means units rather than records, so this
    declines even though `COUNT(quantity)` is the same mistake. The case it
    would fix already passes; widening the trigger to catch it would cost the
    guard that keeps row counts safe.
    """
    sql = "SELECT COUNT(quantity) FROM sales_data"
    assert fix(conn, sql, "How many units were sold in total?") == sql


def test_a_question_that_is_not_a_count_declines(conn):
    sql = "SELECT COUNT(quantity) FROM sales_data WHERE product = 'Laptop'"
    assert fix(conn, sql, "What was the total revenue from laptops?") == sql


def test_a_negated_filter_declines(conn):
    """`product != 'Laptop'` names the value and excludes it. Only equality
    says the query is about the thing the question counts."""
    sql = "SELECT COUNT(quantity) FROM sales_data WHERE product != 'Laptop'"
    assert fix(conn, sql) == sql


def test_an_expression_on_the_column_side_declines(conn):
    """Deliberate limit: the filter has to be a plain column against the value.
    `upper(product) = 'LAPTOP'` is a fair way to write it and is left alone,
    because "some expression equals this string" is a weaker statement than
    "this column holds this value" and the repair rests on the stronger one."""
    sql = "SELECT COUNT(quantity) FROM sales_data WHERE upper(product) = 'LAPTOP'"
    assert fix(conn, sql) == sql


def test_an_irregular_plural_declines(conn):
    """"mice" does not resolve to 'Mouse' — no stemmer, and a miss means no
    rewrite, which is the safe direction."""
    sql = "SELECT COUNT(quantity) FROM sales_data WHERE product = 'Mouse'"
    assert fix(conn, sql, "How many mice were sold?") == sql


def test_an_identifier_measure_declines(conn):
    """Summing `order_id` means nothing. Every value distinct says the column
    identifies the row rather than measuring it — #74's cardinality question."""
    sql = "SELECT COUNT(CASE WHEN product = 'Laptop' THEN order_id END) FROM sales_data"
    assert fix(conn, sql) == sql


def test_a_text_measure_declines(conn):
    """`region` repeats, so the cardinality guard passes it — the EXPLAIN gate
    is what stops SUM over a text column."""
    sql = "SELECT COUNT(CASE WHEN product = 'Laptop' THEN region END) FROM sales_data"
    assert fix(conn, sql) == sql


def test_a_distinct_count_is_left_alone(conn):
    sql = "SELECT COUNT(DISTINCT quantity) FROM sales_data WHERE product = 'Laptop'"
    assert fix(conn, sql) == sql


def test_a_filtered_aggregate_is_left_alone(conn):
    sql = (
        "SELECT COUNT(quantity) FILTER (WHERE product = 'Laptop') FROM sales_data"
    )
    assert fix(conn, sql) == sql


def test_two_projections_are_left_alone(conn):
    sql = (
        "SELECT COUNT(quantity), SUM(total_amount) FROM sales_data "
        "WHERE product = 'Laptop'"
    )
    assert fix(conn, sql) == sql


def test_a_multi_branch_case_is_left_alone(conn):
    """Two WHEN branches is a pivot, not the single conditional this repairs."""
    sql = (
        "SELECT COUNT(CASE WHEN product = 'Laptop' THEN quantity "
        "WHEN product = 'Mouse' THEN total_amount END) FROM sales_data"
    )
    assert fix(conn, sql) == sql


def test_a_grouped_query_is_left_alone(conn):
    sql = (
        "SELECT COUNT(quantity) FROM sales_data WHERE product = 'Laptop' "
        "GROUP BY region"
    )
    assert fix(conn, sql) == sql


def test_a_limited_query_is_left_alone(conn):
    sql = "SELECT quantity FROM sales_data WHERE product = 'Laptop' LIMIT 2"
    assert fix(conn, sql) == sql


def test_a_subquery_source_is_left_alone(conn):
    sql = (
        "SELECT COUNT(quantity) FROM (SELECT * FROM sales_data) t "
        "WHERE product = 'Laptop'"
    )
    assert fix(conn, sql) == sql


def test_a_non_count_aggregate_is_left_alone(conn):
    sql = "SELECT AVG(quantity) FROM sales_data WHERE product = 'Laptop'"
    assert fix(conn, sql) == sql


def test_an_already_summed_query_is_left_alone(conn):
    sql = "SELECT SUM(quantity) FROM sales_data WHERE product = 'Laptop'"
    assert fix(conn, sql) == sql


def test_a_qualified_column_declines(conn):
    sql = "SELECT COUNT(s.quantity) FROM sales_data s WHERE s.product = 'Laptop'"
    assert fix(conn, sql) == sql


# ── Robustness ────────────────────────────────────────────────────────────────


def test_the_repair_is_idempotent(conn):
    once = fix(conn, "SELECT COUNT(quantity) FROM sales_data WHERE product = 'Laptop'")
    assert fix(conn, once) == once


def test_the_rewrite_is_still_safe_sql(conn):
    from app.nl2sql.sql_validator import is_safe_sql

    sql = "SELECT COUNT(quantity) FROM sales_data WHERE product = 'Laptop'"
    assert is_safe_sql(fix(conn, sql))


def test_unparseable_sql_is_returned_unchanged(conn):
    sql = "SELECT COUNT(quantity) FROM WHERE product ="
    assert fix(conn, sql) == sql


def test_a_broken_connection_does_not_raise(conn):
    class Exploding:
        def execute(self, *a, **k):
            raise RuntimeError("boom")

    sql = "SELECT COUNT(quantity) FROM sales_data WHERE product = 'Laptop'"
    assert sum_the_measure_a_count_discarded(sql, QUESTION, Exploding()) == sql
