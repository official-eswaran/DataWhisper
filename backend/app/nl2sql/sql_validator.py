import re


FORBIDDEN_KEYWORDS = ["DROP", "DELETE", "UPDATE", "INSERT", "ALTER", "CREATE", "TRUNCATE"]


def extract_sql(llm_response: str) -> str:
    """Extract SQL query from LLM response."""
    # Try to find SQL in code blocks
    match = re.search(r"```(?:sql)?\s*(.*?)```", llm_response, re.DOTALL)
    if match:
        return match.group(1).strip()

    # Try to find a SELECT statement
    match = re.search(r"(SELECT\s.+)", llm_response, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip().rstrip(";")

    return llm_response.strip()


def is_safe_sql(sql: str) -> bool:
    """Check that SQL only contains safe read operations."""
    upper_sql = sql.upper()
    for keyword in FORBIDDEN_KEYWORDS:
        if re.search(rf"\b{keyword}\b", upper_sql):
            return False
    return True


def validate_and_fix_sql(llm_response: str, conn) -> str | None:
    """Extract, validate, and return safe SQL or None."""
    sql = extract_sql(llm_response)

    if not sql or not is_safe_sql(sql):
        return None

    # Quick syntax check using EXPLAIN
    try:
        conn.execute(f"EXPLAIN {sql}")
        return sql
    except Exception:
        return None
