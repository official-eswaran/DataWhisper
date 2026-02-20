from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.database import get_audit_db
from app.core.security import require_admin

router = APIRouter()


@router.get("/logs")
def get_audit_logs(
    limit: int = 50,
    _admin: Annotated[dict, Depends(require_admin)] = None,
):
    """Retrieve recent audit logs — admin only."""
    conn = get_audit_db()
    rows = conn.execute(
        "SELECT id, username, session_id, natural_query, generated_sql, result_summary, status, created_at "
        "FROM audit_logs ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()

    return [
        {
            "id":        r[0],
            "username":  r[1],
            "session_id": r[2],
            "question":  r[3],
            "sql":       r[4],
            "summary":   r[5],
            "status":    r[6],
            "timestamp": r[7],
        }
        for r in rows
    ]
