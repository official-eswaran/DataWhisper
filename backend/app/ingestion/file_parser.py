import pandas as pd
from pathlib import Path

from app.ingestion.schema_detector import detect_and_clean_schema


def parse_file(file_path: Path) -> pd.DataFrame:
    """Parse uploaded file into a clean DataFrame."""
    suffix = file_path.suffix.lower()

    if suffix == ".csv":
        df = pd.read_csv(file_path)
    elif suffix in (".xlsx", ".xls"):
        df = pd.read_excel(file_path)
    elif suffix == ".json":
        df = pd.read_json(file_path)
    elif suffix == ".parquet":
        df = pd.read_parquet(file_path)
    else:
        raise ValueError(f"Unsupported file type: {suffix}")

    df = detect_and_clean_schema(df)
    return df


def load_dataframe_to_duckdb(conn, df: pd.DataFrame, table_name: str):
    """Load a cleaned DataFrame into DuckDB as a table."""
    conn.execute(f'DROP TABLE IF EXISTS "{table_name}"')
    conn.register("_temp_df", df)
    conn.execute(f'CREATE TABLE "{table_name}" AS SELECT * FROM _temp_df')
    conn.unregister("_temp_df")
