import re

import pandas as pd


def clean_column_name(col: str) -> str:
    """Normalize messy column names to clean SQL-safe names."""
    col = col.strip().lower()
    col = re.sub(r"[^a-z0-9_]", "_", col)
    col = re.sub(r"_+", "_", col).strip("_")
    return col


COLUMN_ALIASES = {
    "emp_nm": "employee_name",
    "emp_name": "employee_name",
    "amt": "amount",
    "qty": "quantity",
    "dt": "date",
    "dob": "date_of_birth",
    "sal": "salary",
    "dept": "department",
    "addr": "address",
    "ph": "phone",
    "mob": "mobile",
    "no": "number",
    "num": "number",
    "desc": "description",
    "yr": "year",
    "mon": "month",
}


def expand_abbreviation(col: str) -> str:
    """Expand known abbreviations in column names."""
    parts = col.split("_")
    expanded = [COLUMN_ALIASES.get(p, p) for p in parts]
    return "_".join(expanded)


def detect_and_clean_schema(df: pd.DataFrame) -> pd.DataFrame:
    """Clean column names and infer better data types."""
    # Clean column names
    df.columns = [expand_abbreviation(clean_column_name(c)) for c in df.columns]

    # Try to convert object columns to datetime.
    #
    # A plain `2021-03-15` column parses to datetime64[ns], which DuckDB stores
    # as TIMESTAMP_NS — an unusual type name the model rarely sees, nudging it
    # toward wrong turns like `join_date LIKE '2021%'` (a bind error). When a
    # column carries no time-of-day component we present it as DATE instead: a
    # familiar type the date-function reference (YEAR(), date_trunc, …) expects.
    # Columns that do carry real times stay as timestamps.
    for col in df.select_dtypes(include=["object"]).columns:
        try:
            parsed = pd.to_datetime(df[col], format="mixed", dayfirst=False)
        except (ValueError, TypeError):
            continue
        non_null = parsed.dropna()
        if not non_null.empty and (non_null.dt.normalize() == non_null).all():
            df[col] = parsed.dt.date  # date-only → DuckDB DATE
        else:
            df[col] = parsed

    # Try to convert object columns to numeric
    for col in df.select_dtypes(include=["object"]).columns:
        try:
            df[col] = pd.to_numeric(df[col].str.replace(",", ""))
        except (ValueError, TypeError, AttributeError):
            pass

    return df
