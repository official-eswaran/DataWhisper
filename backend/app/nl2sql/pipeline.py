from app.nl2sql.prompt_builder import build_nl2sql_prompt
from app.nl2sql.sql_validator import validate_and_fix_sql
from app.nl2sql.llm_client import call_local_llm
from app.nl2sql.intent_classifier import classify_intent, generate_chitchat_response


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
                f"SELECT column_name, data_type FROM information_schema.columns "
                f"WHERE table_name='{table_name}'"
            ).fetchall()
            cols_str = ", ".join([f"{name} ({dtype})" for name, dtype in columns])
            sample = self.conn.execute(
                f"SELECT * FROM {table_name} LIMIT 3"
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

        schema_info = self.get_schema_info()

        # Build the prompt
        prompt = build_nl2sql_prompt(
            question=user_question,
            schema=schema_info,
            history=self.history,
        )

        # Get SQL from LLM
        llm_response = call_local_llm(prompt)
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
            retry_response = call_local_llm(retry_prompt)
            generated_sql = validate_and_fix_sql(retry_response, self.conn)
            if not generated_sql:
                return {"type": "error", "message": str(e), "sql": retry_response}
            result_df = self.conn.execute(generated_sql).fetchdf()

        # Update conversation history
        self.history.append({"role": "user", "content": user_question})
        self.history.append({"role": "assistant", "content": generated_sql})

        # Determine response type
        response_type = self._detect_response_type(result_df)

        return {
            "type": response_type,
            "data": result_df.to_dict(orient="records"),
            "columns": list(result_df.columns),
            "sql": generated_sql,
            "row_count": len(result_df),
            "summary": self._generate_summary(user_question, result_df),
        }

    def _detect_response_type(self, df) -> str:
        """Decide if result is best shown as table, number, or chart."""
        if len(df) == 1 and len(df.columns) == 1:
            return "single_value"
        if len(df.columns) == 2 and len(df) > 2:
            return "chart"
        return "table"

    def _generate_summary(self, question: str, df) -> str:
        """Generate a natural language summary of the result."""
        if len(df) == 0:
            return "No results found for your query."
        if len(df) == 1 and len(df.columns) == 1:
            val = df.iloc[0, 0]
            return f"The answer is: {val}"
        return f"Found {len(df)} rows across {len(df.columns)} columns."
