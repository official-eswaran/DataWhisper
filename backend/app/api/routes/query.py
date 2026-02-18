from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.database import get_user_duckdb, get_audit_db
from app.nl2sql.pipeline import NL2SQLPipeline

router = APIRouter()

# In-memory conversation store (per session)
conversation_store: dict[str, list] = {}


class QueryRequest(BaseModel):
    session_id: str
    question: str


@router.post("/")
async def ask_question(req: QueryRequest):
    """Ask a natural language question about your uploaded data."""
    try:
        conn = get_user_duckdb(req.session_id)
    except Exception:
        raise HTTPException(404, "Session not found. Please upload data first.")

    # Get or create conversation history for this session
    history = conversation_store.setdefault(req.session_id, [])

    pipeline = NL2SQLPipeline(db_conn=conn, conversation_history=history)
    result = pipeline.run(req.question)

    # Update stored history
    conversation_store[req.session_id] = pipeline.history

    # Log to audit trail
    audit_conn = get_audit_db()
    audit_conn.execute(
        "INSERT INTO audit_logs (user_id, natural_query, generated_sql, result_summary, status) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            req.session_id,
            req.question,
            result.get("sql", ""),
            result.get("summary", ""),
            result.get("type", "success"),
        ),
    )
    audit_conn.commit()
    audit_conn.close()

    return result
