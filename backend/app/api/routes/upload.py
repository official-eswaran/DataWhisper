import uuid
import shutil
from pathlib import Path

from fastapi import APIRouter, UploadFile, File, HTTPException

from app.core.config import settings
from app.core.database import get_user_duckdb
from app.ingestion.file_parser import parse_file, load_dataframe_to_duckdb
from app.services.anomaly_detector import detect_anomalies

router = APIRouter()

ALLOWED_EXTENSIONS = {".csv", ".xlsx", ".xls", ".json", ".parquet"}


@router.post("/")
async def upload_file(file: UploadFile = File(...)):
    """Upload a data file and load it into a private DuckDB instance."""
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"Unsupported file type: {ext}")

    # Save file to disk
    session_id = str(uuid.uuid4())
    settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    file_path = settings.UPLOAD_DIR / f"{session_id}{ext}"

    with open(file_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    # Parse and load into DuckDB
    try:
        df = parse_file(file_path)
        table_name = Path(file.filename).stem.lower().replace(" ", "_")
        conn = get_user_duckdb(session_id)
        load_dataframe_to_duckdb(conn, df, table_name)
    except Exception as e:
        raise HTTPException(400, f"Failed to process file: {str(e)}")

    # Run anomaly detection on the uploaded data
    anomalies = detect_anomalies(df, table_name)

    # Get schema info
    columns = list(df.columns)
    dtypes = {col: str(dtype) for col, dtype in df.dtypes.items()}

    return {
        "session_id": session_id,
        "table_name": table_name,
        "rows": len(df),
        "columns": columns,
        "dtypes": dtypes,
        "anomalies": anomalies,
        "message": f"Loaded {len(df)} rows into table '{table_name}'",
    }
