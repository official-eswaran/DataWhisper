from app.nl2sql.llm_client import call_local_llm


# Greetings & assistant questions
CHITCHAT_KEYWORDS = [
    "who are you", "what are you", "your name", "what is your name",
    "how are you", "hello", "hi ", "hey", "good morning", "good evening",
    "thank you", "thanks", "bye", "goodbye", "what can you do",
    "tell me about yourself", "introduce yourself",
    "are you ai", "are you a bot", "are you human", "who made you",
    "who created you", "what's up", "how's it going", "nice to meet",
    "tell me a joke", "sing a song",
]

# PHRASE-based off-topic patterns — multi-word to avoid false positives on column names
# Rule: only add something here if it is UNAMBIGUOUSLY not a data query in any context.
OFF_TOPIC_KEYWORDS = [
    # Cooking / recipes
    "how to cook", "how to make", "recipe for", "cooking instructions",
    # Writing tasks
    "write me a", "write a poem", "write a story", "write an essay",
    "write a song", "write a letter",
    # Explicitly general-knowledge phrases
    "what is the capital of", "what is the meaning of",
    "who is the president", "who is the prime minister",
    "prime minister of", "president of",
    "history of the world", "origin of the word",
    "who invented the", "who discovered the",
    "translate this to", "translate to english", "translate to",
    # Clearly off-topic media/event questions
    "movie review", "song lyrics", "sports score", "cricket score",
    "football score", "match result", "election result", "election results",
    "who won the", "news about",
    # General knowledge questions
    "what is the weather", "weather forecast",
    "explain the concept of", "define the term", "meaning of the word",
    "tell me a joke", "make me laugh",
]

# Data query patterns → SQL pipeline
DATA_KEYWORDS = [
    # Action verbs
    "show", "list", "display", "get", "find", "give", "fetch", "retrieve",
    # Aggregations
    "total", "average", "count", "sum", "max", "min", "calculate", "compute",
    "aggregate", "distinct", "unique",
    # Questions
    "how many", "how much", "which", "what is the total", "what is the average",
    # Business terms
    "revenue", "sales", "profit", "salary", "cost",
    # Grouping / filtering
    "group by", "by region", "by category", "by department", "by month",
    "by country", "by state", "by year", "by quarter", "by city",
    "filter", "between", "greater than", "less than", "more than",
    # Sorting / ranking
    "sort", "order by", "ascending", "descending",
    "highest", "lowest", "most", "least", "top", "bottom", "best", "worst", "rank",
    # Trends / analysis
    "trend", "compare", "growth", "decline", "increase", "decrease",
    "percentage", "ratio", "breakdown", "distribution",
    # Time
    "monthly", "yearly", "quarterly", "weekly", "daily",
    "per region", "per category", "per product", "per city",
    # Data structure
    "rows", "columns", "records", "null", "missing", "empty", "duplicate",
]

# Table/column context words — strong signal that user is asking about their data.
# Include common column name words that are NOT ambiguous in this context.
TABLE_CONTEXT_WORDS = [
    # Standard business columns
    "order", "product", "category", "quantity", "price", "amount",
    "region", "date", "employee", "salary", "department",
    "customer", "revenue", "cost", "profit", "discount", "tax",
    # Data structure words
    "row", "column", "table", "record", "data", "report",
    # Geography — common in datasets
    "country", "state", "city",
    # Demographics — common in datasets
    "age", "gender", "status", "type", "value", "score", "rating",
    # Other common column names
    "code", "name", "id", "language", "sport", "program",
    "stock", "inventory", "transaction", "invoice", "shipment",
]


def classify_intent(question: str) -> str:
    """
    Classify whether a question is:
    - "data_query" → go through NL-to-SQL pipeline
    - "chitchat"   → friendly assistant response
    - "off_topic"  → politely refuse and redirect to data questions

    Priority order:
      1. Short greeting → chitchat
      2. Chitchat keywords → chitchat
      3. Strong data signal → data_query  (checked BEFORE off_topic)
      4. Clear off_topic phrase → off_topic
      5. Weak data signal → data_query
      6. LLM fallback
      7. Default → data_query  (users are here to query their data)
    """
    q_lower = question.strip().lower()

    # 1. Quick check: short greetings (1-3 words)
    if len(q_lower.split()) <= 3 and any(
        q_lower.startswith(kw) for kw in ["hi", "hey", "hello", "bye", "thanks"]
    ):
        return "chitchat"

    # 2. Chitchat keywords
    for keyword in CHITCHAT_KEYWORDS:
        if keyword in q_lower:
            return "chitchat"

    # 3. Score data signals
    data_score = sum(1 for kw in DATA_KEYWORDS if kw in q_lower)
    table_score = sum(1 for kw in TABLE_CONTEXT_WORDS if kw in q_lower)

    # Strong data signal → data_query IMMEDIATELY (before any off_topic check)
    if data_score >= 2:
        return "data_query"
    if data_score >= 1 and table_score >= 1:
        return "data_query"

    # 4. Check for clearly off_topic PHRASES (multi-word — safe from column name collisions)
    for phrase in OFF_TOPIC_KEYWORDS:
        if phrase in q_lower:
            return "off_topic"

    # 5. Weak data signal → still likely data_query in this app context
    if data_score >= 1 or table_score >= 1:
        return "data_query"

    # 6. LLM fallback for truly ambiguous cases
    classify_prompt = f"""You are a classifier for a DATA ASSISTANT app. The user has uploaded a CSV/Excel/database and can ask questions about their data.

Classify this message into EXACTLY one category:
- "data_query": User wants to query, analyze, filter, or explore their uploaded DATA (e.g., "show total sales", "how many rows?", "revenue by month", "list all countries")
- "off_topic": User is asking something CLEARLY UNRELATED to data analysis (e.g., recipes, writing poems, general history questions, celebrity info)
- "chitchat": User is greeting or asking about the assistant itself

User message: "{question}"

Reply with ONLY one word: data_query, off_topic, or chitchat"""

    try:
        response = call_local_llm(classify_prompt).strip().lower()
    except RuntimeError:
        # If LLM is unavailable, attempt data_query so user sees a real error
        return "data_query"

    if "data_query" in response:
        return "data_query"
    if "chitchat" in response:
        return "chitchat"
    if "off_topic" in response:
        return "off_topic"

    # 7. Default → data_query (in this app, users are here to query their data)
    return "data_query"


OFF_TOPIC_RESPONSE = (
    "Sorry, I can only answer questions about your uploaded data. "
    "I'm not a general-purpose chatbot.\n\n"
    "Try asking something like:\n"
    "- \"What is the total revenue?\"\n"
    "- \"Show top 5 products by sales\"\n"
    "- \"Revenue trend by month\"\n"
    "- \"Which region has the highest orders?\""
)


def generate_chitchat_response(question: str) -> str:
    """Generate a friendly response for greetings and identity questions."""
    q_lower = question.strip().lower()

    if any(kw in q_lower for kw in ["your name", "who are you", "what are you"]):
        return (
            "I'm DataWhisper, your private AI data assistant. "
            "I help you explore and analyze your uploaded data using natural language. "
            "Try asking me something about your data, like 'Show total revenue by category'!"
        )

    if any(kw in q_lower for kw in ["how are you", "how's it going", "what's up"]):
        return "I'm running great and ready to analyze your data! Ask me any question about your uploaded dataset."

    if any(kw in q_lower for kw in ["hello", "hi ", "hey", "good morning", "good evening"]):
        return (
            "Hello! I'm DataWhisper, your data assistant. "
            "Ask me anything about your uploaded data — like "
            "'What are the top 5 products?' or 'Show revenue trends'."
        )

    if any(kw in q_lower for kw in ["thank", "thanks"]):
        return "You're welcome! Let me know if you have more questions about your data."

    if any(kw in q_lower for kw in ["what can you do", "what do you do"]):
        return (
            "I can help you analyze your uploaded data using plain English! Here's what I can do:\n"
            "- Answer questions like 'What is the total revenue?'\n"
            "- Generate charts: 'Show sales by region'\n"
            "- Find patterns: 'Which product sold the most?'\n"
            "- Detect anomalies in your data\n"
            "- Export reports as PDF\n\n"
            "Just ask a question about your data!"
        )

    if any(kw in q_lower for kw in ["bye", "goodbye"]):
        return "Goodbye! Your data stays safe and private. Come back anytime!"

    # Default: redirect to data
    return (
        "I'm DataWhisper, your data assistant. "
        "I can only help with questions about your uploaded data. "
        "Try asking something like 'Show total revenue by region'!"
    )
