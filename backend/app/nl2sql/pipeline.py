"""Full NL → SQL → Execute → Format pipeline (non-streaming path).

The streaming route in ``api/routes/query.py`` reuses the same building blocks
(``get_schema_info``, ``execute_with_healing``, and the shared
``result_formatter``) so there is exactly one implementation of each concern.
"""
from __future__ import annotations

import logging

import pandas as pd

from app.core.config import settings
from app.nl2sql.intent_classifier import (
    OFF_TOPIC_RESPONSE,
    classify_intent,
    generate_chitchat_response,
)
from app.nl2sql.llm_client import call_local_llm
from app.nl2sql.prompt_builder import build_nl2sql_prompt
from app.nl2sql.result_formatter import build_result, chat_result
from app.nl2sql.sql_repair import add_missing_group_keys
from app.nl2sql.sql_validator import validate_and_fix_sql

logger = logging.getLogger("datawhisper.pipeline")


def get_schema_info(conn) -> str:
    """Extract all table schemas (+ 3 sample rows) from a DuckDB connection."""
    tables = conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
    ).fetchall()

    parts = []
    for (table_name,) in tables:
        columns = conn.execute(
            "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = ?",
            [table_name],
        ).fetchall()
        cols_str = ", ".join(f"{name} ({dtype})" for name, dtype in columns)
        sample = conn.execute(f'SELECT * FROM "{table_name}" LIMIT 3').fetchdf().to_string(index=False)
        parts.append(f"Table: {table_name}\n  Columns: {cols_str}\n  Sample rows:\n{sample}")
    return "\n\n".join(parts)


def execute_with_healing(conn, sql: str, schema_info: str) -> pd.DataFrame:
    """Run SQL; on failure, ask the LLM to repair it once, then re-run.

    Raises RuntimeError (safe message) if it still cannot execute.
    """
    try:
        return conn.execute(sql).fetchdf()
    except Exception as first_error:
        logger.info("SQL failed, attempting self-heal: %s", first_error)
        retry_prompt = (
            f"The following DuckDB SQL failed:\n{sql}\n\n"
            f"Error: {first_error}\n\nSchema:\n{schema_info}\n\n"
            f"Return ONLY the corrected SQL."
        )
        repaired = validate_and_fix_sql(call_local_llm(retry_prompt), conn)
        if not repaired:
            raise RuntimeError("Could not build a valid query for that question. Please rephrase.")
        repaired = add_missing_group_keys(repaired, conn)
        try:
            return conn.execute(repaired).fetchdf()
        except Exception:
            raise RuntimeError("The query could not be executed on your data. Please rephrase.")


class NL2SQLPipeline:
    def __init__(self, db_conn, conversation_history: list | None = None):
        self.conn = db_conn
        self.history = conversation_history or []

    def get_schema_info(self) -> str:
        return get_schema_info(self.conn)

    def run(self, user_question: str) -> dict:
        intent = classify_intent(user_question)

        if intent == "chitchat":
            response_text = generate_chitchat_response(user_question)
            self.history.append({"role": "user", "content": user_question})
            self.history.append({"role": "assistant", "content": response_text})
            return chat_result(response_text)

        if intent == "off_topic":
            return chat_result(OFF_TOPIC_RESPONSE)

        schema_info = self.get_schema_info()
        prompt = build_nl2sql_prompt(question=user_question, schema=schema_info, history=self.history)

        try:
            llm_response = call_local_llm(prompt)
        except RuntimeError as exc:
            return {"type": "error", "message": str(exc), "sql": None}

        generated_sql = validate_and_fix_sql(llm_response, self.conn)
        if not generated_sql:
            return {
                "type": "error",
                "message": "Could not generate a valid SQL query. Please rephrase.",
                "sql": None,
            }
        # After validation, before execution: the user is shown the SQL that ran.
        generated_sql = add_missing_group_keys(generated_sql, self.conn)

        try:
            result_df = execute_with_healing(self.conn, generated_sql, schema_info)
        except RuntimeError as exc:
            return {"type": "error", "message": str(exc), "sql": generated_sql}

        if len(result_df) > settings.MAX_RESULT_ROWS:
            result_df = result_df.head(settings.MAX_RESULT_ROWS)

        self.history.append({"role": "user", "content": user_question})
        self.history.append({"role": "assistant", "content": generated_sql})
        return build_result(result_df, user_question, generated_sql)
