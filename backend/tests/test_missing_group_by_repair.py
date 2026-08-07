"""Missing-GROUP-BY repair (issue #73).

The natural extension of #52. That repair fixes "GROUP BY present, key missing
from the projection"; this one fixes the model dropping the grouping altogether
and answering a per-group question with a single scalar:

    "What is the total revenue for each category?"
      -> SELECT SUM(total_amount) FROM sales_data

`add_missing_group_keys` cannot help — it keys off an existing GROUP BY, and
there isn't one.

Like #69, the answer is derived rather than guessed: the question names the
dimension, and the name is checked against the table's real columns before
anything is rewritten. Most of the tests below are about what must *not* be
rewritten, because the trigger reads the question and several passing eval cases
use similar phrasing.
"""
from __future__ import annotations

import duckdb
import pytest

from app.nl2sql.sql_repair import add_missing_group_by


@pytest.fixture
def conn():
    c = duckdb.connect(":memory:")
    c.execute(
        "CREATE TABLE sales_data("
        "region VARCHAR, category VARCHAR, product VARCHAR, "
        "quantity INTEGER, total_amount INTEGER)"
    )
    c.execute(
        "INSERT INTO sales_data VALUES "
        "('East','Electronics','Laptop',1,100),"
        "('East','Furniture','Desk',2,200),"
        "('West','Electronics','Mouse',3,300),"
        "('West','Electronics','Laptop',1,400)"
    )
    return c


def fix(conn, sql, question):
    return add_missing_group_by(sql, question, conn)


# ── The reported failures ─────────────────────────────────────────────────────


def test_for_each_adds_the_grouping(conn):
    """sales_revenue_by_category — 0/3. One number for a per-category question."""
    sql = "SELECT SUM(total_amount) AS total_revenue FROM sales_data"
    out = fix(conn, sql, "What is the total revenue for each category?")

    assert conn.execute(sql).fetchall() == [(1000,)]                    # the bug
    assert sorted(conn.execute(out).fetchall()) == [
        ("Electronics", 800), ("Furniture", 200),
    ]


def test_in_each_adds_the_grouping(conn):
    """sales_electronics_by_region — 1/3, a CASE WHEN pivot with no GROUP BY."""
    sql = (
        "SELECT SUM(CASE WHEN category = 'Electronics' THEN total_amount ELSE 0 END) "
        "AS electronics_revenue FROM sales_data"
    )
    out = fix(conn, sql, "What is the total Electronics revenue in each region?")
    assert sorted(conn.execute(out).fetchall()) == [("East", 100), ("West", 700)]


def test_per_phrasing_is_recognised(conn):
    out = fix(conn, "SELECT COUNT(*) FROM sales_data", "How many orders per region?")
    assert sorted(conn.execute(out).fetchall()) == [("East", 2), ("West", 2)]


def test_by_phrasing_is_recognised(conn):
    out = fix(conn, "SELECT SUM(total_amount) FROM sales_data", "Total revenue by category")
    assert sorted(conn.execute(out).fetchall()) == [("Electronics", 800), ("Furniture", 200)]


def test_the_dimension_is_projected_first(conn):
    """Matching #52's ordering — dimension, then the measures."""
    out = fix(
        conn,
        "SELECT SUM(total_amount) FROM sales_data",
        "What is the total revenue for each category?",
    )
    assert conn.execute(out).df().columns[0] == "category"


# ── Must not fire ─────────────────────────────────────────────────────────────


def test_an_existing_group_by_is_left_to_the_other_repair(conn):
    """#52's repair owns that case; two repairs touching one clause is how they
    start disagreeing.

    The dimension is deliberately *not* projected here. With `SELECT region,
    COUNT(*) … GROUP BY region` this test was vacuous — a mutation removing the
    GROUP BY guard left it green, because the projection check declined anyway.
    This shape has only the GROUP BY guard standing between it and a duplicated
    grouping.
    """
    sql = "SELECT COUNT(*) FROM sales_data GROUP BY region"
    assert fix(conn, sql, "How many orders were placed in each region?") == sql


def test_a_question_naming_no_dimension_is_untouched(conn):
    sql = "SELECT SUM(total_amount) FROM sales_data"
    assert fix(conn, sql, "What is the total revenue?") == sql


def test_a_word_that_is_not_a_column_is_untouched(conn):
    """"each customer" cannot group a table with no customer column. Matching
    against real columns is what stops a stray "each" producing a rewrite."""
    sql = "SELECT SUM(total_amount) FROM sales_data"
    assert fix(conn, sql, "What is the total revenue for each customer?") == sql


def test_a_non_aggregate_projection_is_untouched(conn):
    """A plain projection asks for rows, not groups.

    Isolating the aggregate guard took two attempts. Asking "for each order"
    failed at the column lookup (`order` is not a column). Projecting `product`
    failed at the EXPLAIN check, because `SELECT category, product … GROUP BY
    category` does not bind. This shape *would* bind if it were rewritten —
    `SELECT category, category … GROUP BY category` is valid, just nonsense — so
    only the aggregate guard prevents it.
    """
    sql = "SELECT category FROM sales_data"
    assert fix(conn, sql, "Show the sales for each category") == sql


def test_a_non_aggregate_projection_that_would_bind_is_still_untouched(conn):
    """The belt-and-braces case: declined by EXPLAIN even if the aggregate guard
    were removed. Kept because it is the shape a real question produces."""
    sql = "SELECT product FROM sales_data"
    assert fix(conn, sql, "Show the product for each category") == sql


def test_sorted_by_is_not_a_grouping_cue(conn):
    sql = "SELECT SUM(total_amount) FROM sales_data"
    for q in (
        "Show revenue sorted by category",
        "Show revenue ordered by category",
        "Show revenue ranked by category",
    ):
        assert fix(conn, sql, q) == sql, q


def test_two_candidate_dimensions_are_not_guessed_at(conn):
    sql = "SELECT SUM(total_amount) FROM sales_data"
    assert fix(conn, sql, "Total revenue for each category and each region") == sql


def test_a_projected_dimension_needs_no_guard_because_it_cannot_bind(conn):
    """There is no "already projected" check, and there should not be.

    It would need an aggregate, no GROUP BY, and the dimension projected bare —
    which DuckDB refuses, so it can never reach the repair. Pinning that here
    means the day DuckDB changes its mind, this fails rather than the guard
    silently being missed.
    """
    import pytest as _pytest

    with _pytest.raises(Exception, match="GROUP BY"):
        conn.execute("SELECT category, SUM(total_amount) FROM sales_data")


def test_ordered_or_limited_queries_are_untouched(conn):
    """Grouping under an existing ORDER BY/LIMIT reinterprets the query."""
    for sql in (
        "SELECT SUM(total_amount) FROM sales_data ORDER BY 1 DESC",
        "SELECT SUM(total_amount) FROM sales_data LIMIT 1",
    ):
        assert fix(conn, sql, "Total revenue for each category") == sql


def test_star_projection_is_untouched(conn):
    sql = "SELECT * FROM sales_data"
    assert fix(conn, sql, "Show everything for each category") == sql


def test_subqueries_and_joins_are_untouched(conn):
    """Two guards enforce this — the BASE_TABLE type check and the table-name
    lookup that follows it — so mutating either one alone leaves this green.
    That is deliberate redundancy, not an untested line: "which table do I read
    columns from?" has no answer for a join, and failing closed twice is the
    right side to err on. Asserted as behaviour rather than pretending to
    isolate a line.
    """
    for sql in (
        "SELECT SUM(total_amount) FROM (SELECT * FROM sales_data) t",
        "SELECT SUM(a.total_amount) FROM sales_data a JOIN sales_data b ON a.region = b.region",
    ):
        assert fix(conn, sql, "Total revenue for each category") == sql


# ── Robustness ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "phrasing, column",
    [
        ("revenue for each category", "category"),
        ("revenue for each categories", "category"),   # -ies -> -y
        ("revenue by regions", "region"),              # plain -s
        ("revenue per product", "product"),
        ("revenue per products", "product"),
    ],
)
def test_singular_and_plural_both_resolve(conn, phrasing, column):
    """The model's phrasing is not the user's, so bridge the obvious variants.

    Anything this fails to resolve simply matches no column and no rewrite
    happens, which is the safe direction to fail in.
    """
    out = fix(conn, "SELECT SUM(total_amount) FROM sales_data", phrasing)
    assert f"GROUP BY {column}" in out


def test_pluralisation_does_not_maul_words_ending_in_double_s(conn):
    """`str.rstrip('s')` strips every trailing s, turning "class" into "clas" —
    the kind of thing that silently half-works until a column is named badly."""
    from app.nl2sql.sql_repair import _singular_plural_forms

    assert "clas" not in _singular_plural_forms("class")
    assert "categorie" not in _singular_plural_forms("categories")


def test_a_rewrite_that_would_not_bind_is_discarded(conn):
    """The EXPLAIN check. A grouped rewrite can fail to bind where the ungrouped
    original did not, and returning broken SQL is worse than returning wrong SQL."""
    sql = "SELECT SUM(total_amount) FROM sales_data"
    out = fix(conn, sql, "Total revenue for each category")
    conn.execute(out)  # must not raise


def test_unparseable_sql_is_returned_unchanged(conn):
    sql = "SELECT SUM(total_amount) FROM"
    assert fix(conn, sql, "Total revenue for each category") == sql


def test_a_broken_connection_does_not_raise(conn):
    class Exploding:
        def execute(self, *a, **k):
            raise RuntimeError("boom")

    sql = "SELECT SUM(total_amount) FROM sales_data"
    assert add_missing_group_by(sql, "revenue for each category", Exploding()) == sql


def test_repaired_sql_stays_safe(conn):
    from app.nl2sql.sql_validator import is_safe_sql

    out = fix(
        conn, "SELECT SUM(total_amount) FROM sales_data", "Total revenue for each category"
    )
    assert is_safe_sql(out)
