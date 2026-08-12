"""Superlative repair (issue #59).

    "Which product has the lowest unit price?"
      -> SELECT MIN(price) AS min_price FROM sales_data

The user asked *which product* and got `50`. It is the more dangerous half of
the pair with #52: a truncated or unlabelled result invites a second look, a
confident scalar does not — `SELECT MIN(price)` is a well-formed answer to a
question nobody asked.

The prompt rule the issue recommends was tried and reverted (`ranking` 87.5% ->
62.5%: the 3B model copied the example's ASC direction onto "highest"
questions). This repair cannot make that mistake — the direction is read off the
aggregate, not written into a rule and hoped for.

As with #74, most of the tests below are about what must *not* be rewritten.
The trigger reads the question, and "What is the smallest order amount?" is the
same aggregate over the same table for a question that genuinely wants the
number.
"""
from __future__ import annotations

import duckdb
import pytest

from app.nl2sql.sql_repair import replace_bare_extreme_with_ranked_row

QUESTION = "Which product has the lowest unit price?"


@pytest.fixture
def conn():
    """Cheapest product is Mouse at 50; dearest is Laptop at 1500.

    `total_amount` deliberately ranks the rows in a *different* order from
    `price` (Desk's 600 beats Laptop's 1500 on price but not on amount), so a
    test that ordered by the wrong column would not accidentally pass.
    """
    c = duckdb.connect(":memory:")
    c.execute(
        "CREATE TABLE sales_data("
        "order_id INTEGER, region VARCHAR, category VARCHAR, product VARCHAR, "
        "quantity INTEGER, price INTEGER, total_amount INTEGER)"
    )
    c.execute(
        "INSERT INTO sales_data VALUES "
        "(1,'East','Electronics','Laptop',1,1500,1500),"
        "(2,'East','Furniture','Desk',2,300,600),"
        "(3,'West','Electronics','Mouse',4,50,200)"
    )
    return c


def fix(conn, sql, question=QUESTION):
    return replace_bare_extreme_with_ranked_row(sql, question, conn)


# ── The reported failure ──────────────────────────────────────────────────────


def test_min_becomes_the_cheapest_row(conn):
    """sales_cheapest_product — 0/3 every round since the eval existed."""
    sql = "SELECT MIN(price) FROM sales_data"
    out = fix(conn, sql)

    # The bug: a number, with no product attached.
    assert conn.execute(sql).fetchall() == [(50,)]
    assert conn.execute(out).fetchall() == [("Mouse", 50)]


def test_the_aliased_form_the_model_actually_emits_is_repaired(conn):
    """The observed SQL carries an alias — `SELECT MIN(price) AS min_price`."""
    out = fix(conn, "SELECT MIN(price) AS min_price FROM sales_data")
    assert conn.execute(out).fetchall() == [("Mouse", 50)]


def test_max_ranks_the_other_way(conn):
    """The direction comes from the aggregate, which is what the reverted
    prompt rule got wrong on exactly this question."""
    out = fix(
        conn,
        "SELECT MAX(price) FROM sales_data",
        "Which product has the highest unit price?",
    )
    assert conn.execute(out).fetchall() == [("Laptop", 1500)]


def test_the_entity_comes_first_and_the_measure_is_kept(conn):
    out = fix(conn, "SELECT MIN(price) FROM sales_data")
    assert list(conn.execute(out).fetchdf().columns) == ["product", "price"]


def test_a_where_clause_survives_the_rewrite(conn):
    out = fix(conn, "SELECT MIN(price) FROM sales_data WHERE category = 'Electronics'")
    assert conn.execute(out).fetchall() == [("Mouse", 50)]


def test_the_measure_is_ranked_not_some_other_column(conn):
    """Ordering by `total_amount` would answer with Mouse's 200 -> the same row
    here, so the assertion is on the SQL: `price` must be the ranking key."""
    out = fix(
        conn,
        "SELECT MAX(price) FROM sales_data",
        "Which product has the highest unit price?",
    )
    assert "ORDER BY price DESC" in out.replace('"', "")


def test_a_null_measure_cannot_win_the_ranking(conn):
    """MIN ignores NULLs, so the ranked rewrite must too, or the repair would
    turn a right answer into an empty product name."""
    conn.execute("INSERT INTO sales_data VALUES (4,'North','Misc','Cable',1,NULL,NULL)")
    out = fix(conn, "SELECT MIN(price) FROM sales_data")
    assert conn.execute(out).fetchall() == [("Mouse", 50)]


def test_any_real_column_can_be_the_entity(conn):
    out = fix(
        conn,
        "SELECT MAX(total_amount) FROM sales_data",
        "Which region has the largest single order?",
    )
    assert conn.execute(out).fetchall() == [("East", 1500)]


def test_a_plural_entity_resolves_to_the_column(conn):
    out = fix(conn, "SELECT MIN(price) FROM sales_data", "Which products are cheapest?")
    assert conn.execute(out).fetchall() == [("Mouse", 50)]


def test_entity_matching_ignores_case(conn):
    conn.execute("CREATE TABLE t2 AS SELECT product AS Product, price FROM sales_data")
    out = fix(conn, "SELECT MIN(price) FROM t2")
    assert conn.execute(out).fetchall() == [("Mouse", 50)]


def test_the_aggregate_name_is_matched_case_insensitively(conn):
    out = fix(conn, "SELECT min(price) FROM sales_data")
    assert conn.execute(out).fetchall() == [("Mouse", 50)]


# ── What must not be rewritten ────────────────────────────────────────────────


def test_a_question_that_wants_the_number_is_left_alone(conn):
    """sales_min_order / sales_max_order — passing 3/3, and asking for the
    measure itself. No "which", no rewrite."""
    sql = "SELECT MIN(total_amount) FROM sales_data"
    assert fix(conn, sql, "What is the smallest order amount?") == sql


def test_an_accumulated_measure_declines(conn):
    """"Highest total revenue" ranks per-region sums. The row holding the single
    largest order is a different answer, and a plausible one — which is why
    guessing here would be worse than declining. That is #74's shape."""
    sql = "SELECT MAX(total_amount) FROM sales_data"
    assert fix(conn, sql, "Which region has the highest total revenue?") == sql


@pytest.mark.parametrize(
    "question",
    [
        "Which category has the highest average price?",
        "Which region has the most orders?",
        "Which product has the highest revenue per order?",
        "Which category has the largest number of sales?",
        "Which product has the highest count of orders?",
        "Which region sold how many units at the lowest price?",
        "Which product has the lowest median price?",
        "Which region has the lowest combined amount?",
    ],
)
def test_a_measure_that_needs_aggregating_first_declines(conn, question):
    sql = "SELECT MIN(price) FROM sales_data"
    assert fix(conn, sql, question) == sql


def test_a_question_that_merely_mentions_a_column_declines(conn):
    """The interrogative is what makes the entity the *answer*. A question that
    names `product` in passing and still wants the number must not be rewritten
    — dropping the "which" requirement is otherwise a silent widening."""
    sql = "SELECT MIN(price) FROM sales_data"
    assert fix(conn, sql, "What is the lowest price in the product catalog?") == sql


def test_a_noun_that_is_not_a_column_declines(conn):
    """The entity is confirmed against the schema, so a question naming
    something the table does not have produces no rewrite."""
    sql = "SELECT MIN(price) FROM sales_data"
    assert fix(conn, sql, "Which supplier has the lowest unit price?") == sql


def test_two_candidate_entities_decline(conn):
    """"Which product ... which region" names two real columns and there is no
    ground for picking one."""
    sql = "SELECT MIN(price) FROM sales_data"
    assert fix(conn, sql, "Which product in which region has the lowest price?") == sql


def test_the_measure_named_as_the_entity_declines(conn):
    """`SELECT price FROM t ORDER BY price LIMIT 1` is MIN(price) spelled
    longer — a rewrite that changes nothing but the row count."""
    sql = "SELECT MIN(price) FROM sales_data"
    assert fix(conn, sql, "Which price is the lowest?") == sql


@pytest.mark.parametrize("agg", ["AVG", "SUM", "COUNT", "median", "stddev_samp"])
def test_only_min_and_max_are_rewritten(conn, agg):
    sql = f"SELECT {agg}(price) FROM sales_data"
    assert fix(conn, sql) == sql


def test_an_expression_argument_declines(conn):
    """MIN(price * quantity) has no column to order by without inventing one."""
    sql = "SELECT MIN(price * quantity) FROM sales_data"
    assert fix(conn, sql) == sql


def test_a_query_that_already_ranks_is_left_alone(conn):
    """The shape the model produces when it gets the question right. Every
    passing `ranking` case looks like this."""
    sql = "SELECT product, price FROM sales_data ORDER BY price ASC LIMIT 1"
    assert fix(conn, sql) == sql


def test_a_grouped_query_is_left_alone(conn):
    sql = "SELECT product, MIN(price) FROM sales_data GROUP BY product"
    assert fix(conn, sql) == sql


def test_a_grouped_scalar_projection_is_left_alone(conn):
    """#52's territory: the key is missing from the projection, but a GROUP BY
    exists and this repair must not fight that one for it."""
    sql = "SELECT MIN(price) FROM sales_data GROUP BY product"
    assert fix(conn, sql) == sql


def test_a_grouped_query_is_declined_even_when_the_rewrite_would_bind(conn):
    """The guard is what stops this, not the EXPLAIN gate downstream.

    Most grouped shapes fail to bind once the measure is projected raw, which
    hides the guard behind that gate. Here both columns are in the GROUP BY, so
    the rewrite binds — and would silently turn three per-group minima into one
    row.
    """
    sql = "SELECT MIN(price) FROM sales_data GROUP BY product, price"
    assert len(conn.execute(sql).fetchall()) == 3
    assert fix(conn, sql) == sql


def test_an_existing_limit_is_left_alone(conn):
    sql = "SELECT MIN(price) FROM sales_data LIMIT 5"
    assert fix(conn, sql) == sql


def test_a_having_clause_is_left_alone(conn):
    sql = "SELECT MIN(price) FROM sales_data GROUP BY product HAVING COUNT(*) > 1"
    assert fix(conn, sql) == sql


def test_a_qualify_clause_is_left_alone(conn):
    sql = (
        "SELECT MIN(price) FROM sales_data GROUP BY product "
        "QUALIFY ROW_NUMBER() OVER (ORDER BY MIN(price)) = 1"
    )
    assert fix(conn, sql) == sql


def test_two_projections_are_left_alone(conn):
    """If the model projected something alongside the aggregate it has already
    decided what to return, and DuckDB would have rejected a bare column here."""
    sql = "SELECT MIN(price), MAX(price) FROM sales_data"
    assert fix(conn, sql) == sql


def test_a_distinct_aggregate_is_left_alone(conn):
    sql = "SELECT MIN(DISTINCT price) FROM sales_data"
    assert fix(conn, sql) == sql


def test_a_filtered_aggregate_is_left_alone(conn):
    sql = "SELECT MIN(price) FILTER (WHERE quantity > 1) FROM sales_data"
    assert fix(conn, sql) == sql


def test_a_subquery_source_is_left_alone(conn):
    """Not a BASE_TABLE, so the columns cannot be resolved against
    information_schema and the entity cannot be confirmed.

    The explicit BASE_TABLE check is redundant and deliberately kept: DuckDB
    serialises SUBQUERY, JOIN and TABLE_FUNCTION sources without a
    ``table_name``, so the next guard already declines. It survives mutation for
    that reason, and it matches the shape of the four repairs above it.
    """
    sql = "SELECT MIN(price) FROM (SELECT * FROM sales_data) t"
    assert fix(conn, sql) == sql


def test_a_join_source_is_left_alone(conn):
    sql = "SELECT MIN(a.price) FROM sales_data a, sales_data b"
    assert fix(conn, sql) == sql


def test_a_cte_named_source_is_left_alone(conn):
    """A CTE serialises as a BASE_TABLE, so the type check passes and the
    columns are what decline it — `information_schema` knows nothing about it."""
    sql = "WITH cheap AS (SELECT * FROM sales_data) SELECT MIN(price) FROM cheap"
    assert fix(conn, sql) == sql


def test_a_qualified_column_declines(conn):
    sql = "SELECT MIN(s.price) FROM sales_data s"
    assert fix(conn, sql) == sql


# ── Robustness ────────────────────────────────────────────────────────────────


def test_the_repair_is_idempotent(conn):
    once = fix(conn, "SELECT MIN(price) FROM sales_data")
    assert fix(conn, once) == once


def test_the_rewrite_is_still_safe_sql(conn):
    from app.nl2sql.sql_validator import is_safe_sql

    assert is_safe_sql(fix(conn, "SELECT MIN(price) FROM sales_data"))


def test_unparseable_sql_is_returned_unchanged(conn):
    sql = "SELECT MIN(price) FROM"
    assert fix(conn, sql) == sql


def test_a_broken_connection_does_not_raise(conn):
    class Exploding:
        def execute(self, *a, **k):
            raise RuntimeError("boom")

    sql = "SELECT MIN(price) FROM sales_data"
    assert replace_bare_extreme_with_ranked_row(sql, QUESTION, Exploding()) == sql


def test_a_table_without_the_entity_column_declines(conn):
    conn.execute("CREATE TABLE prices(price INTEGER)")
    conn.execute("INSERT INTO prices VALUES (10)")
    sql = "SELECT MIN(price) FROM prices"
    assert fix(conn, sql) == sql


def test_a_rewrite_that_would_not_bind_is_discarded(conn):
    """The EXPLAIN gate. Whatever comes back must run: the user is shown the
    query, so broken SQL is worse than wrong SQL.

    A same-named table in another schema is what makes this reachable —
    `information_schema.columns` is matched on `table_name` alone, so `product`
    is found on a table the query does not read from. The lookup being loose is
    survivable precisely because the rewrite still has to bind.
    """
    conn.execute("CREATE SCHEMA other")
    conn.execute("CREATE TABLE other.prices(product VARCHAR, price INTEGER)")
    conn.execute("CREATE TABLE prices(price INTEGER)")
    conn.execute("INSERT INTO prices VALUES (10)")
    sql = "SELECT MIN(price) FROM prices"
    assert fix(conn, sql) == sql
