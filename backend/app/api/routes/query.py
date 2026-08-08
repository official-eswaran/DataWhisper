"""Natural-language query endpoints (streaming + non-streaming).

Both paths:
* verify the caller owns the session (authorization / IDOR fix),
* reuse the shared pipeline + result_formatter (no duplicated logic),
* cache the per-session schema (uploaded data is immutable per session_id),
* never leak raw exception text to the client.

NOTE: no ``from __future__ import annotations`` — combined with the slowapi
decorator it makes FastAPI misclassify the Pydantic body as a query parameter.
"""
import asyncio
import json
import logging
import re as re_module
import threading
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, field_validator

from app.core.config import settings
from app.core.database import require_user_duckdb, user_can_access_session, write_audit_log
from app.core.quota import QUERIES, ROWS_PROCESSED, enforce_quota
from app.core.quota import record as quota_record
from app.core.ratelimit import limiter
from app.core.security import require_verified_email
from app.core.session_store import conversation_store
from app.nl2sql.intent_classifier import (
    OFF_TOPIC_RESPONSE,
    classify_intent,
    generate_chitchat_response,
)
from app.nl2sql.llm_client import llm_cache_lookup, llm_cache_store, stream_local_llm
from app.nl2sql.pipeline import NL2SQLPipeline, execute_with_healing, get_schema_info
from app.nl2sql.prompt_builder import build_nl2sql_prompt
from app.nl2sql.result_formatter import build_result, chat_result
from app.nl2sql.sql_repair import (
    add_distinct_for_value_listing,
    add_missing_group_by,
    add_missing_group_keys,
    move_aggregate_threshold_to_having,
    repair_date_period_bounds,
)
from app.nl2sql.sql_validator import validate_sql

logger = logging.getLogger("datawhisper.query")
router = APIRouter()

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
    return f"data: {json.dumps(data)}\n\n"


def _authorize(session_id: str, user: dict) -> None:
    """Raise 404 unless the caller may access this session (no info leak on 403)."""
    if not user_can_access_session(
        session_id, user.get("sub", ""), user.get("role", ""), user.get("org_id", -1)
    ):
        # Deliberately 404 (not 403) so users cannot probe others' session ids.
        raise HTTPException(404, "Session not found. Please upload data first.")


def _cached_schema(session_id: str, conn) -> str:
    schema = conversation_store.get_schema(session_id)
    if schema is None:
        schema = get_schema_info(conn)
        conversation_store.set_schema(session_id, schema)
    return schema


async def _iter_llm_tokens(prompt: str):
    """Bridge the sync token generator into async. Yields tokens, then
    ("__done__", full_text); raises RuntimeError on LLM errors."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def _run():
        try:
            for item in stream_local_llm(prompt):
                asyncio.run_coroutine_threadsafe(queue.put(item), loop).result()
        except Exception as exc:  # noqa: BLE001 — forwarded as a sentinel
            asyncio.run_coroutine_threadsafe(queue.put(("__error__", str(exc))), loop).result()
        finally:
            asyncio.run_coroutine_threadsafe(queue.put(("__eos__", None)), loop).result()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    while True:
        item = await queue.get()
        if isinstance(item, tuple):
            tag = item[0]
            if tag == "__error__":
                raise RuntimeError(item[1])
            if tag == "__eos__":
                break
            yield item  # ("__done__", full_text)
            continue
        yield item

    thread.join(timeout=5)


# ── Non-streaming endpoint ────────────────────────────────────────────────────
@router.post("/")
@limiter.limit(settings.RATE_LIMIT_QUERY)
async def ask_question(
    request: Request,
    req: QueryRequest,
    current_user: Annotated[dict, Depends(require_verified_email)],
):
    _authorize(req.session_id, current_user)
    username = current_user.get("sub", "unknown")
    org_id = current_user.get("org_id", -1)
    enforce_quota(org_id, QUERIES)  # per-tenant monthly plan limit
    # Only "are they already over?" — a query's row cost isn't known until it has
    # run, and refusing to return results for work already done helps nobody.
    # Per-query overshoot is bounded by MAX_RESULT_ROWS.
    enforce_quota(org_id, ROWS_PROCESSED)

    try:
        conn = require_user_duckdb(req.session_id)
    except FileNotFoundError:
        raise HTTPException(404, "Session not found. Please upload data first.")

    history = conversation_store.get_history(req.session_id)
    try:
        pipeline = NL2SQLPipeline(db_conn=conn, conversation_history=history)
        result = await asyncio.to_thread(pipeline.run, req.question)
    finally:
        conn.close()

    if result.get("type") != "error" and result.get("sql") is not None:
        conversation_store.append_turn(req.session_id, req.question, result["sql"])
        quota_record(org_id, QUERIES, 1)
        quota_record(org_id, ROWS_PROCESSED, int(result.get("row_count", 0)))
    write_audit_log(
        username, org_id, req.session_id, req.question,
        result.get("sql"), result.get("summary", result.get("message", "")),
        result.get("type", "success"),
    )
    return result


# ── Streaming endpoint (SSE) ──────────────────────────────────────────────────
@router.post("/stream")
@limiter.limit(settings.RATE_LIMIT_QUERY)
async def ask_question_stream(
    request: Request,
    req: QueryRequest,
    current_user: Annotated[dict, Depends(require_verified_email)],
):
    _authorize(req.session_id, current_user)
    username = current_user.get("sub", "unknown")
    org_id = current_user.get("org_id", -1)
    enforce_quota(org_id, QUERIES)  # 429 before opening the stream if over limit
    enforce_quota(org_id, ROWS_PROCESSED)  # same, for the row budget

    async def generate():
        conn = None
        try:
            try:
                conn = require_user_duckdb(req.session_id)
            except FileNotFoundError:
                yield _sse({"stage": "error", "message": "Session not found. Please upload data first."})
                return

            history = conversation_store.get_history(req.session_id)

            # Stage 1 — intent
            yield _sse({"stage": "classifying", "message": "Analyzing your question..."})
            intent = await asyncio.to_thread(classify_intent, req.question)

            if intent == "chitchat":
                text = generate_chitchat_response(req.question)
                conversation_store.append_turn(req.session_id, req.question, text)
                write_audit_log(username, org_id, req.session_id, req.question, None, text, "chat")
                yield _sse({"stage": "done", "result": chat_result(text)})
                return

            if intent == "off_topic":
                write_audit_log(username, org_id, req.session_id, req.question, None, OFF_TOPIC_RESPONSE, "off_topic")
                yield _sse({"stage": "done", "result": chat_result(OFF_TOPIC_RESPONSE)})
                return

            # Stage 2 — schema (cached per session)
            yield _sse({"stage": "analyzing", "message": "Exploring your data structure..."})
            schema_info = await asyncio.to_thread(_cached_schema, req.session_id, conn)
            prompt = build_nl2sql_prompt(question=req.question, schema=schema_info, history=history)

            # Stage 3 — generate SQL. Serve from cache when the identical prompt
            # was seen before (repeated question on immutable session data).
            yield _sse({"stage": "generating", "message": "Crafting the SQL query..."})
            cached = llm_cache_lookup(prompt)
            if cached is not None:
                yield _sse({"stage": "token", "token": cached})
                llm_response = cached
            else:
                llm_response = ""
                try:
                    async for item in _iter_llm_tokens(prompt):
                        if isinstance(item, tuple):
                            llm_response = item[1]
                            break
                        yield _sse({"stage": "token", "token": item})
                except RuntimeError as exc:
                    yield _sse({"stage": "done", "result": {"type": "error", "message": str(exc), "sql": None}})
                    return
                llm_cache_store(prompt, llm_response)

            generated_sql, bind_error = validate_sql(llm_response, conn)
            if not generated_sql:
                yield _sse({"stage": "done", "result": {
                    "type": "error",
                    "message": "Could not generate a valid SQL query. Please rephrase.",
                    "sql": None,
                }})
                return
            # Same repair as the non-streaming pipeline — the SQL streamed back,
            # audited and shown to the user must be the SQL that actually ran.
            generated_sql = add_missing_group_keys(generated_sql, conn)
            generated_sql = add_distinct_for_value_listing(
                generated_sql, req.question, conn
            )
            generated_sql = repair_date_period_bounds(
                generated_sql, req.question, conn
            )
            # A per-group question answered with a single scalar (#73). Adds the
            # key to the projection itself, so order against #52's repair is
            # immaterial — it never leaves a GROUP BY for that one to find.
            generated_sql = add_missing_group_by(
                generated_sql, req.question, conn
            )
            # An aggregate threshold that became a row filter (#74). Requires no
            # GROUP BY and no aggregate in the projection — the shape neither of
            # the two above leaves behind — so the order is immaterial.
            generated_sql = move_aggregate_threshold_to_having(
                generated_sql, req.question, conn
            )

            # Stage 4 — execute (with one self-healing retry). A safe query that
            # failed to bind carries bind_error, routing it into the heal path
            # rather than dead-ending above.
            yield _sse({"stage": "executing", "message": "Running the query on your data..."})
            try:
                result_df = await asyncio.to_thread(
                    execute_with_healing, conn, generated_sql, schema_info, bind_error
                )
            except RuntimeError as exc:
                yield _sse({"stage": "done", "result": {"type": "error", "message": str(exc), "sql": generated_sql}})
                return

            if len(result_df) > settings.MAX_RESULT_ROWS:
                result_df = result_df.head(settings.MAX_RESULT_ROWS)

            conversation_store.append_turn(req.session_id, req.question, generated_sql)
            result = build_result(result_df, req.question, generated_sql)
            quota_record(org_id, QUERIES, 1)
            quota_record(org_id, ROWS_PROCESSED, int(result.get("row_count", 0)))
            write_audit_log(username, org_id, req.session_id, req.question, generated_sql, result["summary"], result["type"])
            yield _sse({"stage": "done", "result": result})

        except Exception:  # noqa: BLE001 — last-resort guard for the stream
            logger.exception("Unhandled error in query stream")
            yield _sse({"stage": "done", "result": {
                "type": "error", "message": "Something went wrong processing your question.", "sql": None,
            }})
        finally:
            if conn:
                conn.close()

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
