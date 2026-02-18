from app.nl2sql.llm_client import call_local_llm


# Greetings & assistant questions
CHITCHAT_KEYWORDS = [
    "who are you", "what are you", "your name", "what is your name",
    "how are you", "hello", "hi ", "hey", "good morning", "good evening",
    "thank you", "thanks", "bye", "goodbye", "help me", "what can you do",
    "tell me about yourself", "introduce yourself", "what do you do",
    "are you ai", "are you a bot", "are you human", "who made you",
    "who created you", "what's up", "how's it going", "nice to meet",
    "tell me a joke", "sing a song",
]

# General knowledge / off-topic patterns — NOT data queries
OFF_TOPIC_KEYWORDS = [
    "who is", "who was", "who are", "what is the capital", "what is the meaning",
    "how to cook", "how to make", "recipe", "write me", "write a",
    "tell me about", "explain what", "define ", "meaning of",
    "president", "prime minister", "country", "planet", "weather",
    "movie", "song", "game", "sport", "cricket", "football",
    "religion", "god", "history of", "invented", "discovered",
    "translate", "language", "poem", "story", "essay",
    "how old", "where is", "when was", "why is the sky",
    "what color", "what year", "who won", "who invented",
    "calculate", "solve", "math", "equation",
    "code", "program", "python", "javascript", "html",
    "news", "politics", "election",
]

# Data query patterns that should go to SQL
DATA_KEYWORDS = [
    "show", "total", "average", "count", "sum", "max", "min", "how many",
    "list", "top", "bottom", "revenue", "sales", "profit", "salary",
    "group by", "by region", "by category", "by department", "by month",
    "filter", "where", "between", "greater", "less than", "sort",
    "highest", "lowest", "most", "least", "trend", "compare",
    "how much", "which product", "which region", "which city",
    "find all", "get all", "display", "give me",
    "order by", "ascending", "descending",
    "percentage", "ratio", "growth", "decline", "increase", "decrease",
    "monthly", "yearly", "quarterly", "weekly", "daily",
    "per region", "per category", "per product", "per city",
    "above", "below", "more than", "less than",
]

# Table/column related words — strong signal for data query
TABLE_CONTEXT_WORDS = [
    "order", "product", "category", "quantity", "price", "amount",
    "region", "date", "employee", "salary", "department", "city",
    "customer", "revenue", "cost", "profit", "discount", "tax",
    "row", "column", "table", "record", "data", "report",
]


def classify_intent(question: str) -> str:
    """
    Classify whether a question is:
    - "data_query" → go through NL-to-SQL pipeline
    - "chitchat"   → friendly assistant response
    - "off_topic"  → politely refuse and redirect to data questions
    """
    q_lower = question.strip().lower()

    # Quick check: short greetings (1-3 words)
    if len(q_lower.split()) <= 3 and any(
        q_lower.startswith(kw.strip()) for kw in ["hi", "hey", "hello", "bye", "thanks"]
    ):
        return "chitchat"

    # Check for chitchat patterns (greetings, identity questions)
    for keyword in CHITCHAT_KEYWORDS:
        if keyword in q_lower:
            return "chitchat"

    # Check for data-related keywords FIRST (higher priority)
    data_score = sum(1 for kw in DATA_KEYWORDS if kw in q_lower)
    table_score = sum(1 for kw in TABLE_CONTEXT_WORDS if kw in q_lower)

    # If question mentions table columns/data terms, it's very likely a data query
    if data_score >= 1 and table_score >= 1:
        return "data_query"

    # Check for off-topic / general knowledge questions
    for keyword in OFF_TOPIC_KEYWORDS:
        if keyword in q_lower:
            return "off_topic"

    # If strong data keywords present, treat as data query
    if data_score >= 2:
        return "data_query"

    # If at least one data keyword, likely data query
    if data_score >= 1:
        return "data_query"

    # If table context words found, probably data query
    if table_score >= 1:
        return "data_query"

    # Last resort: use LLM to classify
    classify_prompt = f"""You are a classifier for a DATA ASSISTANT app. The user has uploaded a database and can ask questions about their data.

Classify this message into EXACTLY one category:
- "data_query": User wants to query, analyze, filter, or explore their uploaded DATA (e.g., "show total sales", "how many rows?", "revenue by month")
- "off_topic": User is asking about something UNRELATED to their uploaded data (e.g., general knowledge, coding help, recipes, news, people, places)
- "chitchat": User is greeting or asking about the assistant itself (e.g., "hi", "what is your name?")

User message: "{question}"

Reply with ONLY one word: data_query, off_topic, or chitchat"""

    response = call_local_llm(classify_prompt).strip().lower()

    if "data_query" in response:
        return "data_query"
    if "off_topic" in response:
        return "off_topic"
    # Default to off_topic for safety (don't run random SQL)
    return "off_topic"


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

    if any(kw in q_lower for kw in ["what can you do", "help", "what do you do"]):
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
