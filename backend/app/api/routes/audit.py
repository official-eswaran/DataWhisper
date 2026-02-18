from fastapi import APIRouter

from app.core.database import get_audit_db

router = APIRouter()


@router.get("/logs")
def get_audit_logs(limit: int = 50):
    """Retrieve recent audit logs — who asked what and when."""
    conn = get_audit_db()
    rows = conn.execute(
        "SELECT id, user_id, natural_query, generated_sql, result_summary, status, created_at "
        "FROM audit_logs ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()

    return [
        {
            "id": r[0],
            "user_id": r[1],
            "question": r[2],
            "sql": r[3],
            "summary": r[4],
            "status": r[5],
            "timestamp": r[6],
        }
        for r in rows
    ]
