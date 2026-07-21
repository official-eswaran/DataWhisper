"""File upload endpoint.

Hardening vs. the previous version:
* Enforces MAX_UPLOAD_SIZE_MB while streaming to disk (was defined but never
  checked — a 500 MB upload would OOM the worker).
* Binds the new session to the uploading user (authorization / ownership).
* Cleans up partial files on failure; sanitizes error messages.

NOTE: no ``from __future__ import annotations`` here — it turns UploadFile into a
ForwardRef that FastAPI cannot resolve for multipart file params.
"""
import logging
import re
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile

from app.core.config import settings
from app.core.database import get_user_duckdb, register_session
from app.core.quota import ROWS_PROCESSED, UPLOADS, enforce_quota
from app.core.quota import record as quota_record
from app.core.ratelimit import limiter
from app.core.security import get_current_user
from app.core.session_store import conversation_store
from app.ingestion.file_parser import load_dataframe_to_duckdb, parse_file
from app.services.anomaly_detector import detect_anomalies

logger = logging.getLogger("datawhisper.upload")
router = APIRouter()

ALLOWED_EXTENSIONS = {".csv", ".xlsx", ".xls", ".json", ".parquet"}
_CHUNK = 1024 * 1024  # 1 MiB

_MAGIC: dict[bytes, str] = {
    b"\x50\x4b\x03\x04": "xlsx/zip",
    b"\xd0\xcf\x11\xe0": "xls",
    b"\x50\x41\x52\x31": "parquet",
}


def _validate_magic(file_bytes: bytes, ext: str) -> bool:
    if ext in (".csv", ".json"):
        try:
            file_bytes[:512].decode("utf-8", errors="strict")
            return True
        except UnicodeDecodeError:
            return False
    return any(file_bytes[:4].startswith(sig) for sig in _MAGIC)


def _safe_table_name(filename: str) -> str:
    raw = Path(filename).stem.lower()
    name = re.sub(r"[^a-z0-9_]", "_", raw)
    name = re.sub(r"_+", "_", name).strip("_")
    if not name or name[0].isdigit():
        name = "t_" + (name or "data")
    return name


async def _stream_to_disk(file: UploadFile, dest: Path) -> None:
    """Copy the upload to disk, aborting if it exceeds the configured limit."""
    written = 0
    limit = settings.max_upload_bytes
    with open(dest, "wb") as out:
        while True:
            chunk = await file.read(_CHUNK)
            if not chunk:
                break
            written += len(chunk)
            if written > limit:
                out.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(
                    413, f"File exceeds the maximum size of {settings.MAX_UPLOAD_SIZE_MB} MB"
                )
            out.write(chunk)


@router.post("/")
@limiter.limit(settings.RATE_LIMIT_UPLOAD)
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    current_user: Annotated[dict, Depends(get_current_user)] = None,
):
    org_id = (current_user or {}).get("org_id", -1)
    enforce_quota(org_id, UPLOADS)  # per-tenant monthly plan limit

    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"Unsupported file type: {ext or 'unknown'}")

    header = await file.read(512)
    await file.seek(0)
    if not _validate_magic(header, ext):
        raise HTTPException(400, "File content does not match the declared extension")

    session_id = str(uuid.uuid4())
    settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    file_path = settings.UPLOAD_DIR / f"{session_id}{ext}"

    await _stream_to_disk(file, file_path)

    try:
        df = parse_file(file_path)
        table_name = _safe_table_name(file.filename or "data")
        conn = get_user_duckdb(session_id)
        try:
            load_dataframe_to_duckdb(conn, df, table_name)
        finally:
            conn.close()
    except HTTPException:
        file_path.unlink(missing_ok=True)
        raise
    except Exception:
        file_path.unlink(missing_ok=True)
        logger.exception("Failed to process upload")
        raise HTTPException(400, "Could not read the uploaded file. Is it a valid, non-corrupt data file?")

    register_session(
        session_id,
        current_user.get("sub", "unknown"),
        current_user.get("org_id", -1),
        table_name,
        len(df),
    )
    quota_record(org_id, UPLOADS, 1)
    quota_record(org_id, ROWS_PROCESSED, len(df))
    conversation_store.invalidate(session_id)

    anomalies = detect_anomalies(df, table_name)
    return {
        "session_id": session_id,
        "table_name": table_name,
        "rows": len(df),
        "columns": list(df.columns),
        "dtypes": {col: str(dtype) for col, dtype in df.dtypes.items()},
        "anomalies": anomalies,
        "message": f"Loaded {len(df)} rows into table '{table_name}'",
    }
