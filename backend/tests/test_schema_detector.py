"""Schema inference — what type the model ultimately sees.

The type name in the schema block steers the SQL the model writes. A date-only
column that lands in DuckDB as TIMESTAMP_NS (an unusual name) nudges the model
toward wrong turns like `join_date LIKE '2021%'`, which fails to bind. These
tests pin the type each kind of column is presented as, through the same DuckDB
round-trip production uses.
"""
import datetime as dt

import duckdb
import pandas as pd

from app.ingestion.file_parser import load_dataframe_to_duckdb
from app.ingestion.schema_detector import detect_and_clean_schema


def _duckdb_types(df: pd.DataFrame) -> dict[str, str]:
    conn = duckdb.connect(":memory:")
    load_dataframe_to_duckdb(conn, df, "t")
    rows = conn.execute(
        "SELECT column_name, data_type FROM information_schema.columns WHERE table_name='t'"
    ).fetchall()
    return dict(rows)


def test_date_only_column_is_presented_as_DATE():
    """The core fix: a plain YYYY-MM-DD column must reach DuckDB as DATE, not
    TIMESTAMP_NS, so YEAR()/date_trunc work and LIKE is never tempting."""
    df = detect_and_clean_schema(
        pd.DataFrame({"join_date": ["2021-03-15", "2020-07-22", "2019-11-10"]})
    )
    assert df["join_date"].map(type).eq(dt.date).all()
    assert _duckdb_types(df)["join_date"] == "DATE"


def test_a_column_with_real_times_stays_a_timestamp():
    """Only date-only columns are narrowed; genuine timestamps keep their time."""
    df = detect_and_clean_schema(
        pd.DataFrame({"created_at": ["2021-03-15 09:30:00", "2020-07-22 14:00:00"]})
    )
    assert _duckdb_types(df)["created_at"].startswith("TIMESTAMP")


def test_year_filter_binds_on_the_detected_date_column():
    """End-to-end: the query the model *should* write actually runs."""
    df = detect_and_clean_schema(
        pd.DataFrame({"join_date": ["2021-03-15", "2020-07-22", "2019-11-10"]})
    )
    conn = duckdb.connect(":memory:")
    load_dataframe_to_duckdb(conn, df, "employees")
    assert conn.execute(
        "SELECT COUNT(*) FROM employees WHERE YEAR(join_date) = 2021"
    ).fetchone()[0] == 1


def test_the_buggy_like_still_fails_to_bind_on_a_date():
    """Documents *why* DATE matters: `LIKE` on a date is a bind error either way,
    so getting the type right is what steers the model off that path."""
    df = detect_and_clean_schema(pd.DataFrame({"join_date": ["2021-03-15"]}))
    conn = duckdb.connect(":memory:")
    load_dataframe_to_duckdb(conn, df, "employees")
    try:
        conn.execute("EXPLAIN SELECT COUNT(*) FROM employees WHERE join_date LIKE '2021%'")
        raise AssertionError("expected a bind error")
    except duckdb.Error:
        pass


def test_numeric_and_string_columns_are_unaffected():
    """Date narrowing must not disturb the other inference paths."""
    df = detect_and_clean_schema(
        pd.DataFrame({"amount": ["1,000", "2,500"], "city": ["Chennai", "Mumbai"]})
    )
    types = _duckdb_types(df)
    assert types["amount"] in ("BIGINT", "HUGEINT", "INTEGER")
    assert types["city"] == "VARCHAR"


def test_a_date_column_with_missing_values_still_becomes_DATE():
    df = detect_and_clean_schema(
        pd.DataFrame({"join_date": ["2021-03-15", None, "2019-11-10"]})
    )
    assert _duckdb_types(df)["join_date"] == "DATE"
