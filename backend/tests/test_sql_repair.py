"""Tests for the GROUP BY key repair (issue #52).

These matter more than usual: the repair reads DuckDB's serialized AST, which is
an internal format that can shift between DuckDB versions. If a future bump
changes the shape, these tests fail loudly here rather than silently turning the
repair into a no-op — which is exactly what a bare ``except`` would otherwise
hide.
"""
from __future__ import annotations

import duckdb
import pytest

from app.nl2sql.sql_repair import add_missing_group_keys


@pytest.fixture
def conn():
    c = duckdb.connect(":memory:")
    c.execute(
        "CREATE TABLE sales_data("
        "region VARCHAR, category VARCHAR, product VARCHAR, "
        "quantity INTEGER, total_amount INTEGER, order_date DATE)"
    )
    c.execute(
        "INSERT INTO sales_data VALUES "
        "('East','Electronics','Laptop',1,100,'2024-01-01'),"
        "('East','Furniture','Desk',2,200,'2024-02-01'),"
        "('West','Electronics','Mouse',3,300,'2024-03-01')"
    )
    return c


def _cols(conn, sql):
    return list(conn.execute(sql).fetchdf().columns)


# ── the bug being fixed ───────────────────────────────────────────────────────

def test_missing_group_key_is_added(conn):
    sql = "SELECT SUM(total_amount) FROM sales_data GROUP BY region"
    out = add_missing_group_keys(sql, conn)
    assert out != sql
    # The point of the repair: the answer becomes readable.
    assert _cols(conn, out)[0] == "region"
    assert conn.execute(out).fetchdf().shape == (2, 2)


def test_count_star_grouped_gets_its_key(conn):
    out = add_missing_group_keys("SELECT COUNT(*) FROM sales_data GROUP BY category", conn)
    assert _cols(conn, out)[0] == "category"


def test_every_missing_key_is_added_for_multi_column_grouping(conn):
    out = add_missing_group_keys(
        "SELECT SUM(total_amount) FROM sales_data GROUP BY region, category", conn
    )
    assert _cols(conn, out)[:2] == ["region", "category"]


def test_only_the_missing_key_is_added(conn):
    out = add_missing_group_keys(
        "SELECT region, SUM(total_amount) FROM sales_data GROUP BY region, category", conn
    )
    assert _cols(conn, out)[:2] == ["category", "region"]


def test_repair_survives_where_order_by_and_limit(conn):
    sql = (
        "SELECT COUNT(*) FROM sales_data WHERE total_amount > 50 "
        "GROUP BY region ORDER BY COUNT(*) DESC LIMIT 1"
    )
    out = add_missing_group_keys(sql, conn)
    frame = conn.execute(out).fetchdf()
    assert list(frame.columns)[0] == "region"
    assert len(frame) == 1


def test_aliases_on_the_aggregate_are_preserved(conn):
    out = add_missing_group_keys(
        "SELECT SUM(total_amount) AS total FROM sales_data GROUP BY region", conn
    )
    assert "total" in _cols(conn, out)


def test_repaired_sql_answers_the_question_correctly(conn):
    out = add_missing_group_keys(
        "SELECT SUM(total_amount) FROM sales_data GROUP BY region", conn
    )
    rows = {r[0]: r[1] for r in conn.execute(out).fetchall()}
    assert rows == {"East": 300, "West": 300}


# ── queries that must be left exactly as they are ─────────────────────────────

@pytest.mark.parametrize(
    "sql",
    [
        # Already correct.
        "SELECT region, SUM(total_amount) FROM sales_data GROUP BY region",
        "SELECT sales_data.region, SUM(total_amount) FROM sales_data GROUP BY sales_data.region",
        "SELECT product FROM sales_data GROUP BY product HAVING SUM(total_amount) > 1",
        # No grouping at all.
        "SELECT SUM(total_amount) FROM sales_data",
        "SELECT * FROM sales_data",
        "SELECT region FROM sales_data",
        # The key is already implied by the projection.
        "SELECT * FROM sales_data GROUP BY ALL",
        # Positional and expression keys: not a plain column reference.
        "SELECT SUM(total_amount) FROM sales_data GROUP BY 1",
        # Multiple grouping sets — rows have different keys populated, so
        # injecting a column would change the meaning, not label it.
        "SELECT SUM(total_amount) FROM sales_data GROUP BY ROLLUP(region, category)",
        "SELECT SUM(total_amount) FROM sales_data GROUP BY CUBE(region, category)",
        "SELECT SUM(total_amount) FROM sales_data GROUP BY GROUPING SETS ((region),(category))",
        # A CTE with no grouping in the *outer* select.
        "WITH t AS (SELECT SUM(total_amount) AS s FROM sales_data GROUP BY region) SELECT * FROM t",
        # SELECT * already projects the grouping key.
        "SELECT * FROM sales_data GROUP BY region",
        # Set operations.
        "SELECT region FROM sales_data UNION SELECT category FROM sales_data",
    ],
)
def test_leaves_these_untouched(conn, sql):
    assert add_missing_group_keys(sql, conn) == sql


def test_unparseable_input_is_returned_unchanged(conn):
    assert add_missing_group_keys("not sql at all", conn) == "not sql at all"
    assert add_missing_group_keys("", conn) == ""


def test_a_broken_connection_does_not_raise(conn):
    class Exploding:
        def execute(self, *args, **kwargs):
            raise RuntimeError("boom")

    sql = "SELECT SUM(total_amount) FROM sales_data GROUP BY region"
    assert add_missing_group_keys(sql, Exploding()) == sql


def test_repair_output_is_still_safe_sql(conn):
    from app.nl2sql.sql_validator import is_safe_sql

    out = add_missing_group_keys("SELECT COUNT(*) FROM sales_data GROUP BY region", conn)
    assert is_safe_sql(out)


def test_repair_is_idempotent(conn):
    once = add_missing_group_keys("SELECT COUNT(*) FROM sales_data GROUP BY region", conn)
    assert add_missing_group_keys(once, conn) == once


def test_star_projection_is_never_given_a_duplicate_column(conn):
    """SELECT * already includes the key; adding it would duplicate a column."""
    sql = "SELECT * FROM sales_data GROUP BY region"
    assert add_missing_group_keys(sql, conn) == sql


def test_outer_grouping_is_repaired_even_inside_a_cte(conn):
    """Only the outermost projection is touched, so a WITH is no different."""
    sql = (
        "WITH recent AS (SELECT * FROM sales_data WHERE total_amount > 50) "
        "SELECT SUM(total_amount) FROM recent GROUP BY region"
    )
    out = add_missing_group_keys(sql, conn)
    assert out != sql
    assert _cols(conn, out)[0] == "region"
    assert conn.execute(out).fetchdf().shape == (2, 2)


# ── Both execution paths must apply the same repairs ──────────────────────────


def _repairs_called_in(module) -> set[str]:
    """The `sql_repair` functions a module actually calls.

    Reads the source rather than the imports: a name can be imported and then
    not called, which is precisely the half-wiring this is here to catch.
    """
    import ast
    import inspect

    import app.nl2sql.sql_repair as sql_repair

    exported = {
        name for name, obj in vars(sql_repair).items()
        if callable(obj) and not name.startswith("_")
        and getattr(obj, "__module__", None) == sql_repair.__name__
    }
    tree = ast.parse(inspect.getsource(module))
    return {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id in exported
    }


def test_the_streaming_and_non_streaming_paths_apply_the_same_repairs():
    """Two call sites, and nothing but this test keeps them in step.

    `pipeline.py` serves the non-streaming path; `query.py` serves the SSE
    stream the UI actually uses. A repair wired into one and not the other is a
    silent half-fix: the eval scores the pipeline and passes, while every real
    user goes through the stream and still sees the bug. That mistake was made
    while adding #74 and caught here.

    Deliberately structural. Asserting the *set* of repairs rather than a list
    of names means the next repair is covered the day it is written, without
    anyone remembering to extend this.
    """
    from app.api.routes import query as query_route
    from app.nl2sql import pipeline

    streaming = _repairs_called_in(query_route)
    non_streaming = _repairs_called_in(pipeline)

    assert streaming, "found no repair calls at all — the AST walk has gone blind"
    assert streaming == non_streaming, (
        f"only in the stream: {streaming - non_streaming}; "
        f"only in the pipeline: {non_streaming - streaming}"
    )
