import re
import uuid
import shutil
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, UploadFile, File, HTTPException

from app.core.config import settings
from app.core.database import get_user_duckdb
from app.core.security import get_current_user
from app.ingestion.file_parser import parse_file, load_dataframe_to_duckdb
from app.services.anomaly_detector import detect_anomalies

router = APIRouter()

ALLOWED_EXTENSIONS = {".csv", ".xlsx", ".xls", ".json", ".parquet"}

# Magic-byte signatures for allowed file types
_MAGIC: dict[bytes, str] = {
    b"\x50\x4b\x03\x04": "xlsx/zip",   # ZIP container (xlsx, xlsm, parquet)
    b"\xd0\xcf\x11\xe0": "xls",        # Compound document (legacy xls)
    b"\x50\x41\x52\x31": "parquet",    # PAR1
}


def _validate_magic(file_bytes: bytes, ext: str) -> bool:
    """Return True if the file's magic bytes are plausible for the declared extension."""
    if ext in (".csv", ".json"):
        # Text formats — just make sure it's not a binary blob
        try:
            file_bytes[:512].decode("utf-8", errors="strict")
            return True
        except UnicodeDecodeError:
            return False
    header = file_bytes[:4]
    return any(header.startswith(sig) for sig in _MAGIC)


@router.post("/")
async def upload_file(
    file: UploadFile = File(...),
    _user: Annotated[dict, Depends(get_current_user)] = None,
):
    """Upload a data file and load it into a private DuckDB instance."""
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"Unsupported file type: {ext}")

    # Read first 512 bytes to validate magic bytes (content ≠ extension mismatch)
    header = await file.read(512)
    await file.seek(0)
    if not _validate_magic(header, ext):
        raise HTTPException(400, "File content does not match the declared extension")

    # Save file to disk
    session_id = str(uuid.uuid4())
    settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    file_path = settings.UPLOAD_DIR / f"{session_id}{ext}"

    with open(file_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    # Parse and load into DuckDB
    try:
        df = parse_file(file_path)
        # Build a safe SQL identifier from the filename
        raw = Path(file.filename).stem.lower()
        table_name = re.sub(r"[^a-z0-9_]", "_", raw)
        table_name = re.sub(r"_+", "_", table_name).strip("_")
        if not table_name or table_name[0].isdigit():
            table_name = "t_" + (table_name or "data")
        conn = get_user_duckdb(session_id)
        try:
            load_dataframe_to_duckdb(conn, df, table_name)
        finally:
            conn.close()
    except HTTPException:
        raise
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
