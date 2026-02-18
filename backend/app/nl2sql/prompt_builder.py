def build_nl2sql_prompt(question: str, schema: str, history: list = None) -> str:
    """Build a structured prompt for NL-to-SQL conversion."""

    history_context = ""
    if history:
        recent = history[-6:]  # Last 3 Q&A pairs for context
        history_context = "\n## Previous Conversation:\n"
        for msg in recent:
            role = "User" if msg["role"] == "user" else "SQL"
            history_context += f"{role}: {msg['content']}\n"

    return f"""You are a SQL expert assistant. Convert the user's natural language
question into a valid SQL query.

## Rules:
1. Return ONLY the SQL query, nothing else
2. Use DuckDB SQL syntax
3. Never use DELETE, UPDATE, INSERT, DROP, ALTER, or CREATE statements
4. Only use SELECT statements
5. Use the exact table and column names from the schema
6. For aggregations, always include meaningful aliases
7. If the question is a follow-up, use the conversation history for context

## Database Schema:
{schema}
{history_context}
## User Question:
{question}

## SQL Query:"""
