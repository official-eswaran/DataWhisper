import math

from app.nl2sql.prompt_builder import build_nl2sql_prompt
from app.nl2sql.sql_validator import validate_and_fix_sql
from app.nl2sql.llm_client import call_local_llm
from app.nl2sql.intent_classifier import classify_intent, generate_chitchat_response, OFF_TOPIC_RESPONSE
from app.visualization.chart_advisor import recommend_chart_type


class NL2SQLPipeline:
    """
    Full pipeline: Natural Language → SQL → Execute → Format Result

    Steps:
    0. Classify intent (data query vs chitchat)
    1. Build prompt with schema context
    2. Send to local LLM (Ollama)
    3. Extract and validate SQL
    4. Execute on DuckDB
    5. Format result for user
    """

    def __init__(self, db_conn, conversation_history: list = None):
        self.conn = db_conn
        self.history = conversation_history or []

    def get_schema_info(self) -> str:
        """Extract all table schemas from the DuckDB connection."""
        tables = self.conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
        ).fetchall()

        schema_parts = []
        for (table_name,) in tables:
            columns = self.conn.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name = ?",
                [table_name],
            ).fetchall()
            cols_str = ", ".join([f"{name} ({dtype})" for name, dtype in columns])
            sample = self.conn.execute(
                f'SELECT * FROM "{table_name}" LIMIT 3'
            ).fetchdf().to_string(index=False)
            schema_parts.append(
                f"Table: {table_name}\n"
                f"  Columns: {cols_str}\n"
                f"  Sample rows:\n{sample}"
            )
        return "\n\n".join(schema_parts)

    def run(self, user_question: str) -> dict:
        """Execute the full NL-to-SQL pipeline."""
        # Step 0: Classify intent — is this a data query or chitchat?
        intent = classify_intent(user_question)

        if intent == "chitchat":
            response_text = generate_chitchat_response(user_question)
            self.history.append({"role": "user", "content": user_question})
            self.history.append({"role": "assistant", "content": response_text})
            return {
                "type": "chat",
                "data": [],
                "columns": [],
                "sql": None,
                "row_count": 0,
                "summary": response_text,
            }

        if intent == "off_topic":
            return {
                "type": "chat",
                "data": [],
                "columns": [],
                "sql": None,
                "row_count": 0,
                "summary": OFF_TOPIC_RESPONSE,
            }

        schema_info = self.get_schema_info()

        # Build the prompt
        prompt = build_nl2sql_prompt(
            question=user_question,
            schema=schema_info,
            history=self.history,
        )

        # Get SQL from LLM
        try:
            llm_response = call_local_llm(prompt)
        except RuntimeError as e:
            return {"type": "error", "message": str(e), "sql": None}

        generated_sql = validate_and_fix_sql(llm_response, self.conn)

        if not generated_sql:
            return {
                "type": "error",
                "message": "Could not generate a valid SQL query. Please rephrase.",
                "sql": llm_response,
            }

        # Execute the query
        try:
            result_df = self.conn.execute(generated_sql).fetchdf()
        except Exception as e:
            # Self-healing: send error back to LLM for correction
            retry_prompt = (
                f"The following SQL failed:\n{generated_sql}\n\n"
                f"Error: {str(e)}\n\n"
                f"Schema:\n{schema_info}\n\n"
                f"Fix the SQL query. Return ONLY the corrected SQL."
            )
            try:
                retry_response = call_local_llm(retry_prompt)
            except RuntimeError as retry_err:
                return {"type": "error", "message": str(retry_err), "sql": None}
            generated_sql = validate_and_fix_sql(retry_response, self.conn)
            if not generated_sql:
                return {"type": "error", "message": str(e), "sql": retry_response}
            try:
                result_df = self.conn.execute(generated_sql).fetchdf()
            except Exception as e2:
                return {"type": "error", "message": str(e2), "sql": generated_sql}

        # Update conversation history
        self.history.append({"role": "user", "content": user_question})
        self.history.append({"role": "assistant", "content": generated_sql})

        # Determine response type
        response_type = self._detect_response_type(result_df, user_question)

        # Replace NaN/Inf with None for JSON serialization
        records = result_df.to_dict(orient="records")
        clean_records = [
            {
                k: (None if isinstance(v, float) and (math.isnan(v) or math.isinf(v)) else v)
                for k, v in row.items()
            }
            for row in records
        ]

        return {
            "type": response_type,
            "data": clean_records,
            "columns": list(result_df.columns),
            "sql": generated_sql,
            "row_count": len(result_df),
            "summary": self._generate_summary(user_question, result_df),
        }

    def _detect_response_type(self, df, question: str = "") -> str:
        """Delegate chart-type recommendation to the visualization advisor."""
        return recommend_chart_type(df, question)

    def _generate_summary(self, question: str, df) -> str:
        """Generate a meaningful natural-language summary of the query result."""
        import pandas as pd

        rows, cols = len(df), len(df.columns)

        if rows == 0:
            return "No results found for your query."

        # Single scalar value
        if rows == 1 and cols == 1:
            val = df.iloc[0, 0]
            col = df.columns[0]
            if isinstance(val, float):
                formatted = f"{val:,.2f}"
            elif isinstance(val, int):
                formatted = f"{val:,}"
            else:
                formatted = str(val)
            return f"{col}: **{formatted}**"

        # Single row, multiple columns — describe as key-value pairs
        if rows == 1:
            parts = []
            for col in df.columns[:5]:
                val = df.iloc[0][col]
                if isinstance(val, float):
                    parts.append(f"{col}: {val:,.2f}")
                elif val is not None:
                    parts.append(f"{col}: {val}")
            return "Result — " + " | ".join(parts)

        # Two-column result (label + value)
        if cols == 2:
            label_col, value_col = df.columns[0], df.columns[1]
            if pd.api.types.is_numeric_dtype(df[value_col]):
                total    = df[value_col].sum()
                top_row  = df.loc[df[value_col].idxmax()]
                top_val  = top_row[value_col]
                top_lbl  = top_row[label_col]
                if isinstance(total, float):
                    total_str = f"{total:,.2f}"
                    top_str   = f"{top_val:,.2f}"
                else:
                    total_str = f"{int(total):,}"
                    top_str   = f"{int(top_val):,}"
                return (
                    f"{rows} results — highest: **{top_lbl}** ({top_str}), "
                    f"total {value_col}: {total_str}"
                )
            return f"{rows} results for {label_col}."

        # Multi-column
        numeric_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
        if numeric_cols:
            summaries = []
            for col in numeric_cols[:3]:
                col_sum = df[col].sum()
                if isinstance(col_sum, float):
                    summaries.append(f"{col} total: {col_sum:,.2f}")
                else:
                    summaries.append(f"{col} total: {int(col_sum):,}")
            return f"{rows} rows — " + " | ".join(summaries)

        return f"Found {rows} rows across {cols} columns."
