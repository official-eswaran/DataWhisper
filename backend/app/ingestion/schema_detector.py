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

    # Try to convert object columns to datetime
    for col in df.select_dtypes(include=["object"]).columns:
        try:
            df[col] = pd.to_datetime(df[col], infer_datetime_format=True)
        except (ValueError, TypeError):
            pass

    # Try to convert object columns to numeric
    for col in df.select_dtypes(include=["object"]).columns:
        try:
            df[col] = pd.to_numeric(df[col].str.replace(",", ""))
        except (ValueError, TypeError, AttributeError):
            pass

    return df
