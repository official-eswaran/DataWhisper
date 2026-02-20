import asyncio
import json
import math
import re as re_module
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, field_validator

from app.core.database import require_user_duckdb, get_audit_db
from app.core.security import get_current_user
from app.nl2sql.pipeline import NL2SQLPipeline
from app.nl2sql.intent_classifier import classify_intent, generate_chitchat_response, OFF_TOPIC_RESPONSE
from app.nl2sql.prompt_builder import build_nl2sql_prompt
from app.nl2sql.llm_client import call_local_llm
from app.nl2sql.sql_validator import validate_and_fix_sql

router = APIRouter()

# In-memory conversation store (per session)
conversation_store: dict[str, list] = {}

_UUID_RE = re_module.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


class QueryRequest(BaseModel):
    session_id: str
    question: str

    @field_validator("session_id")
    @classmethod
    def validate_session_id(cls, v: str) -> str:
        if not _UUID_RE.match(v.lower()):
            raise ValueError("Invalid session_id format")
        return v.lower()

    @field_validator("question")
    @classmethod
    def validate_question(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Question cannot be empty")
        if len(v) > 2000:
            raise ValueError("Question too long (max 2000 characters)")
        return v


def _sse(data: dict) -> str:
    """Format a dict as a Server-Sent Event line."""
    return f"data: {json.dumps(data)}\n\n"


def _log_audit(username: str, session_id: str, question: str, sql: str | None, summary: str, status: str):
    """Write one row to the audit log."""
    conn = get_audit_db()
    conn.execute(
        "INSERT INTO audit_logs (username, session_id, natural_query, generated_sql, result_summary, status) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (username, session_id, question, sql or "", summary, status),
    )
    conn.commit()
    conn.close()


def _clean_records(df) -> list[dict]:
    """Replace NaN/Inf with None so the result is JSON-safe."""
    return [
        {k: (None if isinstance(v, float) and (math.isnan(v) or math.isinf(v)) else v)
         for k, v in row.items()}
        for row in df.to_dict(orient="records")
    ]


# ── Non-streaming endpoint (kept for compatibility) ────────────────────────────
@router.post("/")
async def ask_question(
    req: QueryRequest,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Ask a natural language question about your uploaded data."""
    try:
        conn = require_user_duckdb(req.session_id)
    except FileNotFoundError:
        raise HTTPException(404, "Session not found. Please upload data first.")

    try:
        history = conversation_store.setdefault(req.session_id, [])
        pipeline = NL2SQLPipeline(db_conn=conn, conversation_history=history)
        result = pipeline.run(req.question)
        conversation_store[req.session_id] = pipeline.history
    finally:
        conn.close()

    username = current_user.get("sub", "unknown")
    _log_audit(username, req.session_id, req.question, result.get("sql"), result.get("summary", ""), result.get("type", "success"))
    return result


# ── Streaming endpoint — sends live stage events then the final result ─────────
@router.post("/stream")
async def ask_question_stream(
    req: QueryRequest,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """
    SSE streaming query endpoint.
    Yields stage events so the UI can show what the AI is doing in real time,
    then yields the final result as a 'done' event.
    """

    username = current_user.get("sub", "unknown")

    async def generate():
        conn = None
        try:
            try:
                conn = require_user_duckdb(req.session_id)
            except FileNotFoundError:
                yield _sse({"stage": "error", "message": "Session not found. Please upload data first."})
                return

            history = conversation_store.setdefault(req.session_id, [])

            # ── Stage 1: Classify intent ──────────────────────────────────────
            yield _sse({"stage": "classifying", "message": "Analyzing your question..."})
            intent = await asyncio.to_thread(classify_intent, req.question)

            if intent == "chitchat":
                response_text = generate_chitchat_response(req.question)
                history.append({"role": "user", "content": req.question})
                history.append({"role": "assistant", "content": response_text})
                conversation_store[req.session_id] = history
                result = {"type": "chat", "data": [], "columns": [], "sql": None, "row_count": 0, "summary": response_text}
                _log_audit(username, req.session_id, req.question, None, response_text, "chat")
                yield _sse({"stage": "done", "result": result})
                return

            if intent == "off_topic":
                result = {"type": "chat", "data": [], "columns": [], "sql": None, "row_count": 0, "summary": OFF_TOPIC_RESPONSE}
                _log_audit(username, req.session_id, req.question, None, OFF_TOPIC_RESPONSE, "off_topic")
                yield _sse({"stage": "done", "result": result})
                return

            # ── Stage 2: Load schema ──────────────────────────────────────────
            yield _sse({"stage": "analyzing", "message": "Exploring your data structure..."})
            pipeline = NL2SQLPipeline(db_conn=conn, conversation_history=history)
            schema_info = await asyncio.to_thread(pipeline.get_schema_info)
            prompt = build_nl2sql_prompt(question=req.question, schema=schema_info, history=history)

            # ── Stage 3: LLM generates SQL ────────────────────────────────────
            yield _sse({"stage": "generating", "message": "Crafting the SQL query..."})
            try:
                llm_response = await asyncio.to_thread(call_local_llm, prompt)
            except RuntimeError as e:
                yield _sse({"stage": "done", "result": {"type": "error", "message": str(e), "sql": None}})
                return

            generated_sql = validate_and_fix_sql(llm_response, conn)
            if not generated_sql:
                result = {"type": "error", "message": "Could not generate a valid SQL query. Please rephrase.", "sql": llm_response}
                yield _sse({"stage": "done", "result": result})
                return

            # ── Stage 4: Execute on DuckDB ────────────────────────────────────
            yield _sse({"stage": "executing", "message": "Running the query on your data..."})
            try:
                result_df = conn.execute(generated_sql).fetchdf()
            except Exception as e:
                # Self-healing retry
                yield _sse({"stage": "healing", "message": "Fine-tuning the query..."})
                retry_prompt = (
                    f"The following SQL failed:\n{generated_sql}\n\n"
                    f"Error: {str(e)}\n\nSchema:\n{schema_info}\n\n"
                    f"Fix the SQL. Return ONLY the corrected SQL."
                )
                try:
                    retry_response = await asyncio.to_thread(call_local_llm, retry_prompt)
                except RuntimeError as retry_err:
                    yield _sse({"stage": "done", "result": {"type": "error", "message": str(retry_err), "sql": None}})
                    return

                generated_sql = validate_and_fix_sql(retry_response, conn)
                if not generated_sql:
                    yield _sse({"stage": "done", "result": {"type": "error", "message": str(e), "sql": retry_response}})
                    return
                try:
                    result_df = conn.execute(generated_sql).fetchdf()
                except Exception as e2:
                    yield _sse({"stage": "done", "result": {"type": "error", "message": str(e2), "sql": generated_sql}})
                    return

            # ── Format and return result ──────────────────────────────────────
            history.append({"role": "user", "content": req.question})
            history.append({"role": "assistant", "content": generated_sql})
            conversation_store[req.session_id] = history

            response_type = pipeline._detect_response_type(result_df)
            result = {
                "type": response_type,
                "data": _clean_records(result_df),
                "columns": list(result_df.columns),
                "sql": generated_sql,
                "row_count": len(result_df),
                "summary": pipeline._generate_summary(req.question, result_df),
            }
            _log_audit(username, req.session_id, req.question, generated_sql, result.get("summary", ""), response_type)
            yield _sse({"stage": "done", "result": result})

        finally:
            if conn:
                conn.close()

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
