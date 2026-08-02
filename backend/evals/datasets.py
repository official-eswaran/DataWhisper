"""Load the eval datasets through the real ingestion path.

Deliberately reuses ``parse_file`` + ``load_dataframe_to_duckdb`` rather than a
plain ``read_csv``: column cleaning and type inference in ``schema_detector``
change what the model sees in the schema block, so an eval that bypassed them
would be measuring a pipeline that does not exist in production.
"""
from __future__ import annotations

import functools
from pathlib import Path

import duckdb

from app.ingestion.file_parser import load_dataframe_to_duckdb, parse_file

# repo_root/sample_data — this file is backend/evals/datasets.py
SAMPLE_DATA_DIR = Path(__file__).resolve().parents[2] / "sample_data"

# Table name == CSV stem, matching ``_safe_table_name`` in the upload route.
DATASETS = {
    "sales_data": SAMPLE_DATA_DIR / "sales_data.csv",
    "employees": SAMPLE_DATA_DIR / "employees.csv",
}


@functools.cache
def _load(name: str) -> duckdb.DuckDBPyConnection:
    try:
        csv_path = DATASETS[name]
    except KeyError:
        raise KeyError(f"Unknown dataset {name!r}. Known: {sorted(DATASETS)}") from None
    if not csv_path.exists():
        raise FileNotFoundError(f"Eval dataset missing: {csv_path}")

    conn = duckdb.connect(":memory:")
    load_dataframe_to_duckdb(conn, parse_file(csv_path), name)
    return conn


def get_connection(name: str) -> duckdb.DuckDBPyConnection:
    """Return the shared in-memory connection for a dataset.

    One connection per dataset, holding exactly one table, because that is the
    shape a real session has: an upload creates a single table and the schema
    block the model sees describes only that table.
    """
    return _load(name)


def reset() -> None:
    """Drop cached connections (tests)."""
    _load.cache_clear()
