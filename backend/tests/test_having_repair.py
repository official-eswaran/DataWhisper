"""Aggregate-threshold repair (issue #74).

`having` was the only eval category at 0/3, and it is one case failing every run:

    "Which products generated more than 200000 in revenue?"
      -> SELECT product FROM sales_data WHERE total_amount > 200000

The threshold applies to each product's *total*, which exists only after
grouping. Tested against individual orders it is wrong in both directions and
says nothing about it: a product whose revenue accumulates over many small
orders is missed, and one whose single large order clears the bar is reported
even when the question was about totals.

Most of the tests below are about what must *not* be rewritten. The trigger has
to read both the question and the data, and three passing eval cases use the
identical sentence shape — "Which orders had a total amount above 100000?" is
correct as a row filter and must survive untouched.
"""
from __future__ import annotations

import duckdb
import pytest

from app.nl2sql.sql_repair import move_aggregate_threshold_to_having

QUESTION = "Which products generated more than 200 in revenue?"


@pytest.fixture
def conn():
    """`order_id` identifies a row; `product` names a group. That is the whole
    distinction the repair turns on, so the fixture makes both shapes available.

    Revenue by product: Laptop 250 (one order), Mouse 300 (three of 100), Desk
    50. With a `> 200` threshold the row filter returns Laptop alone — Mouse,
    the largest earner, never appears.
    """
    c = duckdb.connect(":memory:")
    c.execute(
        "CREATE TABLE sales_data("
        "order_id INTEGER, product VARCHAR, region VARCHAR, "
        "price INTEGER, total_amount INTEGER)"
    )
    c.execute(
        "INSERT INTO sales_data VALUES "
        "(1,'Laptop','East',250,250),"
        "(2,'Mouse','East',10,100),"
        "(3,'Mouse','West',10,100),"
        "(4,'Mouse','North',10,100),"
        "(5,'Desk','West',50,50)"
    )
    return c


def fix(conn, sql, question=QUESTION):
    return move_aggregate_threshold_to_having(sql, question, conn)


# ── The reported failure ──────────────────────────────────────────────────────


def test_the_threshold_moves_to_having_over_the_sum(conn):
    """sales_products_over_200k — 0/3, the only category at zero."""
    sql = "SELECT product FROM sales_data WHERE total_amount > 200"
    out = fix(conn, sql)

    # The bug: Mouse earned the most and is absent.
    assert conn.execute(sql).fetchall() == [("Laptop",)]
    assert sorted(conn.execute(out).fetchall()) == [("Laptop",), ("Mouse",)]


def test_it_matches_the_reference_query(conn):
    """Pinned against the eval's own reference SQL rather than a hand-typed
    answer, which is how `evals/cases.py` scores this case."""
    out = fix(conn, "SELECT product FROM sales_data WHERE total_amount > 200")
    reference = (
        "SELECT product FROM sales_data GROUP BY product HAVING SUM(total_amount) > 200"
    )
    assert sorted(conn.execute(out).fetchall()) == sorted(conn.execute(reference).fetchall())


def test_the_other_direction_of_the_error_is_fixed_too(conn):
    """A `<` threshold shows the false-positive half: Mouse totals 300, but
    every one of its orders is under 200, so the row filter reports it."""
    sql = "SELECT product FROM sales_data WHERE total_amount < 200"
    out = fix(conn, sql, "Which products totalled less than 200 in revenue?")

    assert ("Mouse",) in conn.execute(sql).fetchall()          # the bug
    assert sorted(conn.execute(out).fetchall()) == [("Desk",)]


def test_the_grouped_result_no_longer_repeats_the_entity(conn):
    """The row filter returns one row per matching order. Grouping is what makes
    the answer a list of products rather than a list of orders wearing product
    names."""
    sql = "SELECT product FROM sales_data WHERE total_amount < 200"
    out = fix(conn, sql, "Which products totalled less than 200 in revenue?")

    assert conn.execute(sql).fetchall().count(("Mouse",)) == 3
    assert len(conn.execute(out).fetchall()) == len(set(conn.execute(out).fetchall()))


@pytest.mark.parametrize(
    "question",
    [
        "Which products generated more than 200 in revenue?",
        "Which products had total revenue over 200?",
        "Which products totalled more than 200?",
        "Which products sold 200 or more worth of stock combined?",
        "Which products brought in more than 200 altogether?",
        "Which products have cumulative revenue above 200?",
    ],
)
def test_accumulation_vocabulary_is_recognised(conn, question):
    out = fix(conn, "SELECT product FROM sales_data WHERE total_amount > 200", question)
    assert "GROUP BY" in out and "sum(total_amount)" in out


@pytest.mark.parametrize("op", [">", ">=", "<", "<="])
def test_every_inequality_is_carried_across(conn, op):
    sql = f"SELECT product FROM sales_data WHERE total_amount {op} 200"
    out = fix(conn, sql, "Which products totalled that much in revenue?")
    assert f"sum(total_amount) {op} 200" in out


def test_the_original_where_clause_is_removed_not_duplicated(conn):
    """Leaving the row filter in place would pre-filter the rows being summed —
    a different query again, and one that still misses Mouse."""
    out = fix(conn, "SELECT product FROM sales_data WHERE total_amount > 200")
    assert "WHERE" not in out.upper()


# ── Must not fire: the question names a different aggregate ───────────────────


def test_average_phrasing_is_not_guessed_at(conn):
    """The one inference this repair cannot derive is *which* aggregate. When
    the question names another one, declining is the only defensible move."""
    sql = "SELECT product FROM sales_data WHERE total_amount > 200"
    for question in (
        "Which products averaged more than 200 in revenue?",
        "Which products have an average order above 200?",
        "Which products have a mean revenue over 200?",
        "Which products have a median total above 200?",
    ):
        assert fix(conn, sql, question) == sql, question


def test_min_max_phrasing_is_not_guessed_at(conn):
    sql = "SELECT product FROM sales_data WHERE total_amount > 200"
    for question in (
        "Which products have a maximum total above 200?",
        "Which products have their highest total over 200?",
        "Which products have a minimum total over 200?",
    ):
        assert fix(conn, sql, question) == sql, question


def test_per_group_phrasing_belongs_to_the_other_repair(conn):
    """"per" and "each" name a second dimension; #73 owns those questions."""
    sql = "SELECT product FROM sales_data WHERE total_amount > 200"
    for question in (
        "Which products totalled more than 200 in each region?",
        "Which products totalled more than 200 per region?",
    ):
        assert fix(conn, sql, question) == sql, question


def test_a_question_with_no_accumulation_vocabulary_is_untouched(conn):
    """`SELECT product … WHERE price > 20` is the *correct* answer to "which
    products cost more than 20". Summing unit prices across orders would be
    meaningless, and only the vocabulary gate stands between the two."""
    sql = "SELECT product FROM sales_data WHERE price > 20"
    assert fix(conn, sql, "Which products cost more than 20?") == sql


# ── Must not fire: the data says the threshold is row-level ───────────────────


def test_a_unique_entity_column_means_the_row_filter_was_right(conn):
    """sales_large_orders — "Which orders had a total amount above 100000?",
    passing today. The question says "total" exactly like the target case does,
    so the words cannot separate them. `order_id` identifying a row is what
    does: a per-order threshold *is* a row filter.
    """
    sql = "SELECT order_id FROM sales_data WHERE total_amount > 200"
    assert fix(conn, sql, "Which orders had a total amount above 200?") == sql


def test_a_unique_entity_column_is_declined_even_on_target_vocabulary(conn):
    """The vocabulary gate is not what saves the case above. With the question
    reworded to the target's own phrasing, the cardinality check is the only
    guard left standing."""
    sql = "SELECT order_id FROM sales_data WHERE total_amount > 200"
    assert fix(conn, sql, "Which orders generated more than 200 in revenue?") == sql


def test_an_empty_table_says_nothing_and_is_declined(conn):
    """No evidence is not evidence of a dimension.

    This needs no dedicated guard and does not have one: an empty table counts
    (0, 0), so the `distinct < total` test already declines. An earlier
    `bool(total) and …` in front of it was dead code — this test passed with it
    removed, which is how it was found.
    """
    conn.execute("CREATE TABLE empty_sales AS SELECT * FROM sales_data WHERE false")
    sql = "SELECT product FROM empty_sales WHERE total_amount > 200"
    assert fix(conn, sql) == sql


def test_column_names_resolve_case_insensitively(conn):
    """The model does not echo the schema's casing. Resolving through
    `information_schema` is what bridges that — and what keeps the interpolated
    cardinality probe fed from a whitelist rather than from model output."""
    out = fix(conn, "SELECT PRODUCT FROM sales_data WHERE TOTAL_AMOUNT > 200")
    assert sorted(conn.execute(out).fetchall()) == [("Laptop",), ("Mouse",)]


def test_grouping_a_column_by_itself_is_declined(conn):
    """`GROUP BY total_amount HAVING SUM(total_amount) > 200` parses, binds, and
    means nothing."""
    sql = "SELECT total_amount FROM sales_data WHERE total_amount > 200"
    assert fix(conn, sql, "Which totals sum to more than 200?") == sql


def test_a_column_the_table_does_not_have_is_declined(conn):
    """Serialising does not bind, so an unknown column reaches the repair intact.

    Asserted as behaviour rather than pretending to isolate a line: EXPLAIN
    catches this too, and no shape separates them — an unknown *entity* cannot
    produce a `GROUP BY` that binds, and an unknown *measure* cannot produce a
    `SUM` that binds. The `information_schema` lookup is kept regardless,
    because it is also what makes the interpolated cardinality probe a
    whitelist; see `test_the_cardinality_probe_cannot_be_fed_model_output`.
    """
    for sql in (
        "SELECT product FROM sales_data WHERE nonexistent_column > 200",
        "SELECT nonexistent_column FROM sales_data WHERE total_amount > 200",
    ):
        assert fix(conn, sql) == sql, sql


def test_the_cardinality_probe_cannot_be_fed_model_output(conn):
    """The one query in this module built by string interpolation.

    Two things keep it safe and both are load-bearing: the identifier is
    resolved against `information_schema` first, so it is a name the table
    actually has, and it is quoted on the way in. Neither alone is enough —
    a real column can still be named something that needs quoting.
    """
    from app.nl2sql.sql_repair import _quoted, _repeats_across_rows

    assert _quoted('a"b') == '"a""b"'
    conn.execute('CREATE TABLE weird ("a""b" VARCHAR)')
    conn.execute("""INSERT INTO weird VALUES ('x'), ('x')""")
    assert _repeats_across_rows(conn, "weird", 'a"b') is True


# ── Must not fire: the SQL is a different shape ───────────────────────────────


def test_an_existing_group_by_or_having_is_untouched(conn):
    for sql in (
        "SELECT product FROM sales_data GROUP BY product HAVING SUM(total_amount) > 200",
        "SELECT product FROM sales_data WHERE total_amount > 200 GROUP BY product",
    ):
        assert fix(conn, sql) == sql, sql


def test_ordered_limited_or_distinct_queries_are_untouched(conn):
    """A ranked or truncated query is answering a different question, and
    DISTINCT means the model already thought about duplicates."""
    for sql in (
        "SELECT product FROM sales_data WHERE total_amount > 200 ORDER BY product",
        "SELECT product FROM sales_data WHERE total_amount > 200 LIMIT 3",
        "SELECT DISTINCT product FROM sales_data WHERE total_amount > 200",
    ):
        assert fix(conn, sql) == sql, sql


def test_an_aggregating_projection_is_untouched(conn):
    """The query already collapses rows, so this is not the reported defect."""
    sql = "SELECT COUNT(product) FROM sales_data WHERE total_amount > 200"
    assert fix(conn, sql) == sql


def test_a_two_column_projection_is_untouched(conn):
    """Which of the two is the entity is a guess, and guessing is what #59 and
    #60 were reverted for.

    The second shape is the one that isolates the guard. `SELECT product, region
    … GROUP BY product` does not bind, so EXPLAIN would decline it anyway;
    `SELECT product, COUNT(*) … GROUP BY product` binds perfectly well, so only
    the single-column check stops it.
    """
    for sql in (
        "SELECT product, region FROM sales_data WHERE total_amount > 200",
        "SELECT product, COUNT(*) FROM sales_data WHERE total_amount > 200",
    ):
        assert fix(conn, sql) == sql, sql


def test_a_star_projection_is_untouched(conn):
    sql = "SELECT * FROM sales_data WHERE total_amount > 200"
    assert fix(conn, sql) == sql


def test_a_compound_where_clause_is_untouched(conn):
    """Splitting predicates between WHERE and HAVING is a bigger transform than
    the reported defect asks for: `region = 'East'` must stay in WHERE while the
    threshold moves, and getting that wrong changes the answer silently."""
    for sql in (
        "SELECT product FROM sales_data WHERE region = 'East' AND total_amount > 200",
        "SELECT product FROM sales_data WHERE total_amount > 200 OR total_amount < 10",
    ):
        assert fix(conn, sql) == sql, sql


def test_a_query_with_no_where_clause_is_untouched(conn):
    sql = "SELECT product FROM sales_data"
    assert fix(conn, sql) == sql


def test_an_equality_predicate_is_untouched(conn):
    """"more than X" is a threshold. `= X` is not the shape the issue reports,
    and an equality test against a sum is rarely what anyone means."""
    sql = "SELECT product FROM sales_data WHERE total_amount = 200"
    assert fix(conn, sql) == sql


def test_a_date_comparison_is_left_to_the_date_repair(conn):
    """A date literal serialises as a VARCHAR constant. `SUM(order_date)` does
    not even bind, but declining on the literal's type is the clearer place to
    stop — #69 owns these."""
    conn.execute("CREATE TABLE t AS SELECT 'Laptop' AS product, DATE '2020-01-01' AS d")
    sql = "SELECT product FROM t WHERE d < '2020-01-01'"
    assert fix(conn, sql, "Which products totalled anything before 2020?") == sql


def test_a_string_comparison_is_untouched(conn):
    sql = "SELECT region FROM sales_data WHERE product > 'Laptop'"
    assert fix(conn, sql, "Which regions total more than Laptop?") == sql


def test_a_quoted_number_is_declined_even_though_it_would_bind(conn):
    """The isolating case for the numeric-literal check.

    The two tests above are both caught by EXPLAIN as well — `SUM(order_date)`
    and `SUM(product)` do not bind. `WHERE total_amount > '200'` does: DuckDB
    casts the string happily, so the rewrite would run and silently answer a
    question about a threshold whose type the model was confused about.
    """
    sql = "SELECT product FROM sales_data WHERE total_amount > '200'"
    conn.execute(sql)  # binds today — the guard is not standing in for a crash
    assert fix(conn, sql) == sql


def test_joins_and_subqueries_are_untouched(conn):
    """"Which table do I read cardinality from?" has no answer here.

    Two guards enforce it — the BASE_TABLE type check and the `table_name`
    lookup right after — so mutating either alone leaves this green. Deliberate
    redundancy, matching #73's repair: failing closed twice is the right side to
    err on when the question has no answer at all. Asserted as behaviour rather
    than pretending to isolate a line.
    """
    for sql in (
        "SELECT product FROM (SELECT * FROM sales_data) t WHERE total_amount > 200",
        "SELECT a.product FROM sales_data a JOIN sales_data b ON a.product = b.product "
        "WHERE a.total_amount > 200",
    ):
        assert fix(conn, sql) == sql, sql


def test_a_qualified_column_reference_is_untouched(conn):
    """With one base table a qualifier adds nothing, and resolving it against
    `information_schema` correctly is not worth guessing at.

    Two guards enforce this, one on each side of the comparison, so the fully
    qualified form stays green if either is mutated alone. The mixed form
    isolates the projection's — `GROUP BY sales_data.product` binds, so nothing
    downstream would object.
    """
    for sql in (
        "SELECT sales_data.product FROM sales_data WHERE sales_data.total_amount > 200",
        "SELECT sales_data.product FROM sales_data WHERE total_amount > 200",
        "SELECT product FROM sales_data WHERE sales_data.total_amount > 200",
    ):
        assert fix(conn, sql) == sql, sql


# ── Robustness ────────────────────────────────────────────────────────────────


def test_a_column_name_needing_quotes_is_still_measured(conn):
    """The cardinality probe interpolates the column name, so an identifier that
    is not a bare word must survive it. Unquoted, `COUNT(DISTINCT product name)`
    is a syntax error and the repair would silently decline."""
    conn.execute(
        'CREATE TABLE odd AS SELECT product AS "product name", total_amount FROM sales_data'
    )
    sql = 'SELECT "product name" FROM odd WHERE total_amount > 200'
    out = fix(conn, sql)
    assert "GROUP BY" in out
    assert sorted(conn.execute(out).fetchall()) == [("Laptop",), ("Mouse",)]


def test_the_repair_is_idempotent(conn):
    once = fix(conn, "SELECT product FROM sales_data WHERE total_amount > 200")
    assert fix(conn, once) == once


def test_unparseable_sql_is_returned_unchanged(conn):
    sql = "SELECT product FROM WHERE total_amount >"
    assert fix(conn, sql) == sql


def test_a_broken_connection_does_not_raise(conn):
    class Exploding:
        def execute(self, *a, **k):
            raise RuntimeError("boom")

    sql = "SELECT product FROM sales_data WHERE total_amount > 200"
    assert move_aggregate_threshold_to_having(sql, QUESTION, Exploding()) == sql


def test_a_rewrite_that_would_not_bind_is_discarded(conn):
    """The EXPLAIN gate. Whatever comes back must run, because returning broken
    SQL is worse than returning wrong SQL — the user is shown the query.

    `WHERE product > 200` is a numeric threshold against a VARCHAR column. It
    clears every guard above — both names are real columns, the literal is
    numeric, `region` repeats across rows — and `SUM(product)` is what fails.
    Serialising does not bind, so SQL this broken really does reach here.
    """
    sql = "SELECT region FROM sales_data WHERE product > 200"
    with pytest.raises(Exception, match="VARCHAR"):
        conn.execute(sql)
    assert fix(conn, sql, "Which regions generated more than 200 in revenue?") == sql


def test_the_repaired_query_runs(conn):
    out = fix(conn, "SELECT product FROM sales_data WHERE total_amount > 200")
    conn.execute(out)  # must not raise


def test_repaired_sql_stays_safe(conn):
    """No mutation can kill this one, and that is the point.

    The input was already through the safety gate and the output is rebuilt by
    DuckDB's own deserialiser from that same AST, so the re-check cannot fail
    by construction — exactly as in #52's repair. It is kept because "cannot
    fail today" rests on the serialiser round-trip being faithful, and the
    safety gate should not be the thing taking that on trust.
    """
    from app.nl2sql.sql_validator import is_safe_sql

    out = fix(conn, "SELECT product FROM sales_data WHERE total_amount > 200")
    assert is_safe_sql(out)
